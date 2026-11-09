# Engineering Notes: RAHBAR Technical Decisions & Post-Mortem

This document provides in-depth technical analysis for the seven key engineering questions in Section 7 of the case study, citing concrete source files, line numbers, and empirical metrics.

---

## 1. Indexing in API Startup: What Breaks with 3 Replicas and One Chroma Volume?

### The Failure
Placing document indexing inside the API's startup hook (e.g., FastAPI's `@app.on_event("startup")` or `lifespan`) creates a fatal multi-writer race condition. When the `api` Deployment rolls out 3 replicas concurrently:
1. **SQLite Database Corruption**: ChromaDB stores vector index graphs and SQLite metadata under a persistent path (`/chroma/chroma`). Three replica pods will concurrently acquire file locks and write chunks to the same PVC. SQLite is single-writer; concurrent transactions result in `database is locked` panics or silent index corruption where half-written document partitions are lost.
2. **Readiness Probe Failure**: If an API pod reports ready before indexing completes, it serves empty retrievals (`answered_from_context: false`). If it waits for indexing, processing 246 pages takes minutes, exceeding the kubelet's startup probe deadline and triggering an endless pod restart storm.
3. **Restart Stampede**: Any pod restart (due to node eviction, HPA scale-up, or rolling update) forces the new pod to re-extract and re-embed all 246 pages, imposing unnecessary GPU/API billing costs.

### The Object and Manifest Lines Preventing It
We eliminate this failure by decoupling ingestion into a dedicated one-shot Kubernetes `Job`:
- **Object**: `Job` named `indexer` in [`k8s/base/indexer.yaml`](file:///e:/scd2/k8s/base/indexer.yaml#L1-L47).
- **Enforcement**: In [`k8s/base/api.yaml`](file:///e:/scd2/k8s/base/api.yaml#L72-L75), the API Deployment only mounts an ephemeral `emptyDir` at `/tmp`. The API container possesses zero write access to the Chroma PVC and operates strictly as a read-only consumer via `chromadb.HttpClient` in [`api/app/retrieval/chroma.py`](file:///e:/scd2/api/app/retrieval/chroma.py#L48-L58).

---

## 2. Readiness and Liveness Swapped, Chroma Slow for 90 Seconds: What Happens?

### The Scenario
If the probe configurations are reversed—such that `livenessProbe` checks ChromaDB reachability and `readinessProbe` checks only process survival (`/healthz`):
1. **Cluster-Wide Restart Storm**: When Chroma experiences a temporary 90-second latency spike (due to heavy compaction or GC pauses), the kubelet's liveness check (`failureThreshold: 3`, `periodSeconds: 20` in [`k8s/base/api.yaml`](file:///e:/scd2/k8s/base/api.yaml#L56-L61)) fails after 60 seconds across all 3 API replicas.
2. **Cascading Failure**: The kubelet forcefully kills all 3 API containers (`SIGKILL`) and restarts them. Upon booting, the new containers immediately execute liveness probes against the still-slow Chroma server, fail again, and enter `CrashLoopBackOff`. What was merely a temporary slowdown in a downstream dependency becomes a total cluster-wide service outage.

### Our Architecture
In [`api/app/routes/health.py`](file:///e:/scd2/api/app/routes/health.py#L11-L15), `/healthz` tests process liveness without touching dependencies. In [`api/app/routes/health.py`](file:///e:/scd2/api/app/routes/health.py#L17-L70), `/readyz` checks Chroma reachability, non-empty collection counts, and embedding pins. If Chroma slows down, `/readyz` returns 503; the endpoints controller gracefully isolates the API pods from incoming traffic without killing the process, allowing existing requests to finish draining.

---

## 3. What Does a CPU-Based HPA Measure on a Generation Service, and What Should Scale It?

### The 8% Paradox
In [`k8s/base/hpa.yaml`](file:///e:/scd2/k8s/base/hpa.yaml#L1-L24), the HPA targets 70% CPU utilization. However, generation services are primarily **I/O-bound**:
- When the API processes a query, it spends 95% of its execution time blocked on network sockets waiting for HTTP responses from the LLM provider (`httpx.post` in [`api/app/providers/llm/ollama.py`](file:///e:/scd2/api/app/providers/llm/ollama.py#L55-L66)).
- As evidenced in our cluster measurements in [`docs/evidence/kubectl-top.txt`](file:///e:/scd2/docs/evidence/kubectl-top.txt#L1-L10), API pods consume only 11m to 15m CPU out of a 250m request ($\approx 5\%$ to $6\%$ CPU utilization).
- Consequently, under high concurrent traffic, the API's worker threads can become completely saturated with hung I/O connections while CPU utilization remains well below 10%, meaning a CPU-based HPA fails to trigger scaling.

### The Correct Scaling Signal
A generation service should scale on **in-flight concurrency** or **request queue latency**:
- **Metric**: `http_requests_in_flight` or average queue latency.
- **Implementation**: Deploying KEDA (Kubernetes Event-driven Autoscaling) or the Prometheus Adapter querying `sum(rate(rahbar_requests_total[1m]))` or active worker concurrency from `/metrics` ([`api/app/routes/metrics.py`](file:///e:/scd2/api/app/routes/metrics.py#L14-L35)).

### Single-Node Cluster Capacity Ceiling
On a single-node k3d cluster the HPA ceiling is bounded by node capacity; 3 is the tested value, and the manifest keeps `maxReplicas: 8` for the general multi-node production case but documents the single-node ceiling. When running on a laptop k3d environment (total allocatable memory ~3.66 GiB), scaling to 8 replicas (each requesting 512Mi = 4096Mi total) exceeds node allocatable memory, causing newly created pods to enter `Pending` state and CPU exhaustion to trigger node `NodeNotReady` conditions and liveness probe timeouts. Live operation pins `maxReplicas: 3` (`minReplicas: 3`) to match available single-node resources.

---

## 4. The Indexer's Peak Memory, Its Limit, the Margin, and the Exit Code When It Is Wrong

### Empirical Measurements
Guided by `kubectl top pods` output documented in [`docs/evidence/kubectl-top.txt`](file:///e:/scd2/docs/evidence/kubectl-top.txt#L1-L10):
- **Observed Peak Memory**: `892Mi` during PDF page conversion and batch vector generation across the 85-page Annual Report.
- **Configured Resource Limit**: `3Gi` (`3072Mi`) set in [`k8s/base/indexer.yaml`](file:///e:/scd2/k8s/base/indexer.yaml#L46).
- **Safety Margin**: `3072Mi - 892Mi = 2180Mi` ($\approx 2.4\times$ margin above peak usage).
- **Failure Exit Code**: If memory limits are set too tight (e.g. 512Mi), the Linux cgroup OOM killer terminates the indexer process with **Exit Code 137** (`128 + SIGKILL (9)`). The pod enters `CrashLoopBackOff`.

---

## 5. Recall@5 by Question Type: Where Is It Worst, and Why?

### Empirical Results
From [`eval/results.json`](file:///e:/scd2/eval/results.json#L1-L14), measured across our 15 golden questions:
- **Ordinary Questions**: 88.0%
- **Version-Sensitive Questions**: 85.0%
- **Exact-Term Questions**: **75.0% (Worst Performing Category)**

### Theoretical Explanation
Dense embedding models (such as `nomic-embed-text`) project semantic meaning into continuous vector spaces. While excellent at mapping conceptual synonyms (e.g., mapping *"failing prerequisite"* to *"academic probation"*), dense embeddings struggle with exact numerical tokens, alphanumeric clause IDs, and discrete thresholds:
- The vector distance between "Clause 1.21" and "Clause 1.22" is virtually zero, as is the distance between "2.00 CGPA" and "2.20 CGPA".
- Dense vector similarity blurs discrete boundary values into generic semantic neighborhoods.

### Lecture Technique to Address It
The solution presented in the AI Engineering / RAG lecture is **Hybrid Retrieval**:
- Combining dense semantic embeddings with sparse lexical search (BM25 / inverted indexes).
- Merging the candidate rank lists using **Reciprocal Rank Fusion (RRF)**:
  $$RRF\_Score(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$
  Lexical search pins the exact clause or CGPA threshold, while dense search ensures contextual topic matching.

---

## 6. EMBED_MODEL Changed on the API Only: What Does Your System Do?

### Guardrail Behavior
If an administrator changes `EMBED_MODEL` from `nomic-embed-text` to `all-MiniLM-L6-v2` in the ConfigMap and restarts only the API:
1. **Startup Detection**: During initialization in [`api/app/main.py`](file:///e:/scd2/api/app/main.py#L36-L55), the API queries Chroma's collection metadata and compares `col_meta["embed_model"]` with `settings.embed_model`.
2. **Conflict Logging**: The API logs an explicit error:
   `startup check failed: embedding_model mismatch: Chroma collection 'rahbar' was indexed with 'nomic-embed-text' (dim 768), but API is configured with 'all-MiniLM-L6-v2'`.
3. **Readiness Refusal**: In [`api/app/routes/health.py`](file:///e:/scd2/api/app/routes/health.py#L31-L39), `/readyz` intercepts the mismatch flag and returns `HTTP 503 Service Unavailable`.
4. **Endpoint Isolation**: Kubernetes observes the 503 readiness failure and keeps the pod at `0/1 READY`, preventing user queries from being routed to a broken pod.

### What Would Happen Without the Guardrail
Without this check, the API would embed incoming user queries into a 384-dimensional space while Chroma evaluates distance against 768-dimensional document vectors (or compares vectors across entirely different latent coordinate systems). The database returns arbitrary, nonsense chunks with pseudo-random similarity scores, causing the LLM to hallucinate answers without throwing a single runtime exception.

---

## 7. The Failure That Cost Us More Than an Hour

### Symptoms
During the implementation of Stage 5 (`store.py`), running our unit tests and fixture indexing commands resulted in a hard crash:
`RuntimeError: Chroma is running in http-only client mode, and can only be run with 'chromadb.api.fastapi.FastAPI' as the chroma_api_impl.`

### Initial Wrong Belief
We initially believed that our Python environment had a corrupted native SQLite installation or was missing the C++ build tools required for Chroma's local HNSW backend. We spent considerable time attempting to reinstall Python packages and testing in-memory client flags (`is_persistent=False`).

### The Truth
The exact requirement in `indexer/requirements.txt` specifically pins `chromadb-client==1.0.12` (the lightweight, thin HTTP client designed for containers talking to the `chromadb/chroma` server). The thin client does not package the in-process SQLite engine by design.
Inspecting the call stack revealed that calling `chromadb.Client()` triggers local in-process mode, which is prohibited in `chromadb-client`. The fix was straightforward: instantiate `chromadb.HttpClient(host=..., port=...)` for network deployment, and write a clean `FakeChromaClient` in [`indexer/tests/test_pipeline.py`](file:///e:/scd2/indexer/tests/test_pipeline.py#L27-L45) for offline unit testing without spinning up an external daemon.
