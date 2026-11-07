# ADR 0001: Decoupling Indexing as a Batch Job from API Serving

## Status
Accepted · November 2026

## Context
In a cloud-native RAG deployment on Kubernetes, documents must be converted into vector embeddings and written to a vector database (ChromaDB). Placing the document ingestion and embedding pipeline inside the web application startup lifecycle (e.g. in FastAPI's startup hook or `lifespan`) is a common anti-pattern that catastrophic failures in production:
1. **Multi-Writer Volume Corruption**: Running 3 API replicas with startup indexing creates three concurrent writers competing for a single shared Chroma PVC. Chroma SQLite/HNSW storage is not designed for multi-leader concurrent writes; racing writers will corrupt the vector index.
2. **Restart Stampede**: Every node drain, rolling update, HPA scale-up, or OOMKill forces all new API pods to re-extract, clean, chunk, and embed the entire 246-page corpus before accepting traffic, generating massive duplicate CPU/memory spikes and external API token consumption.
3. **Falsified Readiness Probes**: Either the API pod lies and reports ready before indexing completes (serving empty/hallucinated answers to users), or the startup probe times out while waiting for 50+ pages of PDF embeddings, causing Kubernetes to enter a `CrashLoopBackOff` restart storm.
4. **Coupled Deployment Lifecycle**: Re-indexing modified documents requires a full redeployment of the API service, violating independent release principles.

## Decision
We decouple document ingestion completely from query serving:
1. The **Indexer runs as a standalone Kubernetes `Job`** (and scheduled weekly via `CronJob` `reindex`). It runs once, executes the 5-stage extraction/cleaning/chunking/embedding/storing pipeline, records metadata into Chroma, invalidates the semantic cache, and exits with code 0.
2. The **API runs strictly in read-only mode**. The API never mutates the vector database.
3. The API's `/readyz` readiness probe checks that the Chroma collection exists and is non-empty (`count() > 0`), and confirms the embedding model matches `EMBED_MODEL`. If the index is missing or being initially populated, the API pod remains withheld from the Service endpoints without crashing.

## Options Considered
1. **Indexing during API Pod Startup (`lifespan`)**: Rejected. Introduces concurrency races on the PVC, causes multi-minute boot delays, and triggers cascading restart storms during pod eviction.
2. **InitContainer in the API Pod**: Rejected. While an InitContainer finishes before the main container starts, running 3 API replicas still launches 3 concurrent InitContainers attempting to write to the same volume concurrently.
3. **Dedicated Indexer Job with Shared ClusterIP Service**: Accepted. Guarantees single-writer consistency, decouples index maintenance from query serving, and allows on-demand re-indexing (`kubectl create job --from=cronjob/reindex`) with zero API downtime.

## Consequences
- **Positive**: Single-writer safety guarantees index integrity. Zero downtime during re-indexing. API pods boot in $<1$ second because they only read existing collections.
- **Negative**: Requires orchestrating an initial Job execution before the API can report ready.
- **Monitoring**: We monitor Job completion metrics and alert if the indexer exits non-zero or exceeds memory thresholds.
