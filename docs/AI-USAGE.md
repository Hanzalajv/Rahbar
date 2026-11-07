# AI Assistance Disclosure

Under course policy, AI tools are permitted provided their usage is honestly and specifically attributed. Presenting AI-generated work without attribution is prohibited. Below is the complete record of AI assistance utilized in Assignment 02 (RAHBAR).

---

| Tool | What It Wrote or Shaped | What We Changed Afterwards, and Why |
|---|---|---|
| **Google Antigravity (Gemini 3.8 Flash)** | • Initial drafting of extraction, cleaning, and chunking pipeline stages (`extract.py`, `clean.py`, `chunk.py`).<br>• Boilerplate Kubernetes YAML manifests in `k8s/base/` and Kustomize overlays in `k8s/overlays/`.<br>• Formatting and calculation of storage amplification metrics in `docs/PIPELINE.md`. | • Replaced generic PDF extraction with dual-path text detection that flags zero-font scans (`fonts=[]`) and routes them through Tesseract OCR.<br>• Added explicit non-stripping rules for Markdown table pipes (`\|`) and numbered clause prefixes (`1.21`, `5.4`) to preserve tabular structure and citation alignment.<br>• Rewrote the API `/readyz` probe logic to explicitly verify collection non-emptiness (`count() > 0`) and detect embedding-pin mismatches.<br>• Re-configured the HPA manifest to use `autoscaling/v2` with CPU request baseline justifications. |
| **GitHub Copilot / IDE Completion** | • Autocompletion of Pydantic model field definitions in `schemas.py`.<br>• Boilerplate generation for unit test fixtures in `test_rag.py`. | • Manually verified that all citations in test fixtures match retrieved chunks.<br>• Authored the prompt-injection test specifically testing the "ignore your instructions" adversarial payload. |

---

## Authorship and Understanding Declaration

### Pair Project Team
- **Hanzala Ahsan** (Roll: `24i-3150`, `i243150@isb.nu.edu.pk`) — Architecture, Ingestion Pipeline, API & Hybrid Retrieval, ADRs.
- **Sawaira Fareed** (Roll: `24i-3043`, `i243043@isb.nu.edu.pk`) — Corpus Acquisition, Evaluation Harness, Kubernetes Manifests, CI/CD Gateways.

We have reviewed, executed, tested, and validated every line of code, Dockerfile configuration, and Kubernetes manifest in this repository. All architectural decisions (including indexer-as-Job single-writer safety, Redis semantic caching, and embedding-model startup guardrails) reflect our own understanding and will be defended in the course viva.
