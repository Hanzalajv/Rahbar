# Empirical RAG Failure Analysis: Breaking Six Stages

This document records deliberate failure injections performed across the RAG lifecycle. As emphasized in the course lectures: *when a RAG system produces an incorrect or ungrounded response, only Stage F is the language model's fault. Stages A through E are engineering failures in the data pipeline.*

---

## Stage A · Extraction Failure

- **What we broke**: Disabled the OCR fallback in `indexer/pipeline/extract.py` and indexed `faculty-entrepreneurship-and-technical-start-ups-policy.pdf` without rasterization.
- **Symptom**: Questions regarding university faculty technical startup equity shares resulted in immediate honest abstention (`answered_from_context: false`), or returned irrelevant passages from unrelated academic policies.
- **Diagnosis (as if not caused deliberately)**:
  - Command: Run `pdffonts corpus/raw/faculty-entrepreneurship-and-technical-start-ups-policy.pdf`. It returns 0 font objects.
  - Inspection: Extracting text via `pypdf` yields empty strings `""` for all 8 pages.
  - Log Line: The indexer log recorded `{"logger": "rahbar.indexer", "msg": "chunked", "source": "faculty-entrepreneurship-and-technical-start-ups-policy.pdf", "chunks": 0}`.
- **Remedy**: Integrate `pdf2image` and `pytesseract` to detect zero-text pages and perform OCR rasterization, marking `Page.ocr = True`.

---

## Stage B · Chunking Failure

- **What we broke**: Switched `indexer/pipeline/chunk.py` to fixed-size 100-token windows with 0 overlap, disabling section-aware boundary detection.
- **Symptom**: Retrieval recall@5 dropped precipitously from 85% to 54%. Multi-part regulations (e.g., Clause 5.4 Academic Warning conditions and GPA probation thresholds) were bisected across arbitrary chunk boundaries.
- **Diagnosis (as if not caused deliberately)**:
  - Metric: `eval/run_eval.py` reported recall@5 regression (-31pp below baseline).
  - Storage Metric: Chunk count spiked from 618 to over 1,400 chunks.
  - Log Line: API query logs showed retrieved chunks containing truncated sentences such as *"provided that the student has not exceeded the maximum allowable"* without the preceding subject or subsequent penalty clause.
- **Remedy**: Restore structure-aware chunking (`chunk_size=400`, `chunk_overlap=50`) splitting along section headers, paragraph double-newlines, and full sentences.

---

## Stage C · Retrieval & Pin Failure

- **What we broke**: Injected an mismatched model name `EMBED_MODEL=all-MiniLM-L6-v2` into the API ConfigMap while Chroma retained chunks embedded with `nomic-embed-text`.
- **Symptom**: The API refused to enter the `READY` state. Kubernetes withheld all API pods from Service endpoints (0/3 READY).
- **Diagnosis (as if not caused deliberately)**:
  - Command: `kubectl describe pod api-xxxx -n rahbar` revealed `Readiness probe failed: HTTP probe failed with statuscode: 503`.
  - Application Log: `api/app/main.py` logged:
    `{"logger": "rahbar.api", "level": "ERROR", "msg": "startup check failed: embedding_model mismatch: Chroma collection 'rahbar' was indexed with 'nomic-embed-text' (dim 768), but API is configured with 'all-MiniLM-L6-v2'"}`.
- **Remedy**: Synchronize `EMBED_MODEL` in the centralized ConfigMap and verify collection metadata matches before accepting traffic.

---

## Stage D · Prompt Grounding Failure

- **What we broke**: Removed the system instruction *"Answer strictly and solely using the provided retrieved evidence passages"* and omitted boundary tags.
- **Symptom**: For the must-abstain hostel question (*"What is the hostel curfew time for boys and girls?"*), the model answered fluently with fictitious 10:00 PM curfews based on generic training data rather than declining.
- **Diagnosis (as if not caused deliberately)**:
  - Metric: CI evaluation gate failed: `Abstention correctness failed: 50.0% (required: 100.0%)`.
  - Metric: `rahbar_abstentions_total` failed to increment on out-of-scope golden set prompts.
  - Citation Inspection: Returned answer cited `academic-rules-handbook-jun-2026.pdf` for facts that did not exist in the text.
- **Remedy**: Reinforce system instructions with explicit negative constraints (*"If evidence does not contain sufficient facts, return answered_from_context=false"*), wrap retrieved evidence in strict XML/markdown boundaries, and filter citations against retrieved passage content.

---

## Stage E · Source Temporal Governance Failure

- **What we broke**: Removed the metadata filter `where={"superseded": False}` in `ChromaRetriever.retrieve`.
- **Symptom**: When asked *"Under current rules, how are repeated course grades counted in CGPA?"*, the retriever fetched Clause 4.2 from the superseded 2023 rules rather than the 2026 handbook, and the model quoted repealed 2023 policies as current law.
- **Diagnosis (as if not caused deliberately)**:
  - Evaluation: The version-sensitive test query `q09` failed: retrieved chunks were tagged with `doc_version: "2023-08"` and `superseded: true`.
  - Log Line: Query log showed chunk ID `undergraduate-academic-rules-aug-2023-p004-c0008` returned with score 0.88.
- **Remedy**: Enforce hard database-level metadata filtering (`{"superseded": False}`) for all queries unless the user prompt explicitly requests historical or repealed versions.

---

## Stage F · Generation Temperature Degradation

- **What we broke**: Raised `TEMPERATURE` in the API ConfigMap from 0.2 to 1.2.
- **Symptom**: High validation failure rate. The model generated conversational preamble ("Sure, here is your answer:"), emitted invalid JSON with trailing commas, and hallucinated fabricated section titles.
- **Diagnosis (as if not caused deliberately)**:
  - Metric: `rahbar_fallbacks_total` escalated rapidly in Prometheus.
  - Log Line: `api/app/providers/ollama` logged: `ValidationError: 1 validation error for Answer - Invalid JSON syntax`.
  - Retrieval Scores: Retrieval scores remained healthy (scores > 0.85), proving the error occurred entirely in generation.
- **Remedy**: Pin temperature between 0.0 and 0.2 in `k8s/base/configmap.yaml` with explicit documentation that deterministic fact extraction requires low entropy.
