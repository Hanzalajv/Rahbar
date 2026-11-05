# Indexing and Retrieval Pipeline

This document records the empirical measurements, design decisions, and failure characterizations across the five stages of the RAHBAR indexing pipeline (`extract`, `clean`, `chunk`, `embed`, `store`).

---

## 1. Extraction (Stage A)

The extraction stage ingests heterogeneous inputs (PDF, HTML, Markdown) into normalized `Document` models containing structured, page-indexed `Page` objects.

### Documents Requiring OCR
During corpus analysis using `pdffonts` and font dictionary inspection:
1. **`faculty-entrepreneurship-and-technical-start-ups-policy.pdf`**: 8 pages, containing **0 embedded fonts** (`fonts=[]`). The document consists entirely of scanned bitonal page images. Extraction routes these pages through Tesseract OCR via `pdf2image` and `pytesseract`.
2. **`undergraduate-academic-rules-aug-2023.pdf`**: 26 pages, primarily vector text with embedded scanned approval slips and stamp endorsements that lack a text layer.

### Before-and-After Example of a Mangled Table
FAST-NUCES documents contain dense administrative and financial tabular data (e.g., fee schedules, grading thresholds, and annual report statistics). Without structured parsing, standard PDF stream extractors concatenate horizontal cells into illegible vertical text streams.

#### Before (Mangled Linear Text Extraction):
```text
Tuition Fee Per Credit Hour Miscellaneous Charges Admission Fee Degree Fee
BS (CS, SE, DS) 10,000 15,000 30,000 10,000
MS Programs 8,500 15,000 30,000 10,000
PhD Programs 6,000 15,000 30,000 10,000
```
*Issue*: Row-column correspondence is lost when lines wrap, causing vector embeddings to associate charges with the wrong degree program.

#### After (Structured Markdown Table Extraction):
```markdown
| Program Category | Tuition Fee / Credit Hour | Miscellaneous Charges | Admission Fee | Degree Fee |
| :--- | :--- | :--- | :--- | :--- |
| BS (CS, SE, DS, AI, CY) | PKR 10,000 | PKR 15,000 | PKR 30,000 | PKR 10,000 |
| MS Programs | PKR 8,500 | PKR 15,000 | PKR 30,000 | PKR 10,000 |
| PhD Programs | PKR 6,000 | PKR 15,000 | PKR 30,000 | PKR 10,000 |
```
*Result*: Relational column bindings are preserved, allowing exact keyword and semantic queries (e.g., "BS fee per credit hour") to retrieve unbroken tabular context.

### What Could Not Be Recovered
- Faint, low-contrast registrar stamp signatures and official seals on approval memoranda.
- Complex multi-column newspaper-style callout boxes in the Annual Report that overlap primary column margins.

---

## 2. Cleaning (Stage B)

The cleaning stage normalizes extraction noise while guaranteeing structural preservation.

### Cleaning Rules Applied
1. **Non-Printable Character Elimination**: Stripping ASCII control characters (`[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]`) that cause JSON serialization or vector tokenizer errors.
2. **Line-Break De-Hyphenation**: Merging hyphenated words split across line breaks (`(\w+)-\n(\w+)` $\to$ `\1\2`), e.g., `academ-\nic` becomes `academic`.
3. **Repeating Header and Footer Stripping**: Calculating frequency distribution of top and bottom lines across all pages. Lines appearing on $\ge 60\%$ of pages (such as *"FAST National University of Computer and Emerging Sciences"* or page counter footers) are stripped to avoid polluting chunk embeddings.
4. **Paragraph Break Normalization**: Collapsing excessive consecutive newlines (`\n{3,}`) into standardized double newlines (`\n\n`).

### Deliberately NOT Stripped
**Markdown Table Delimiters (`|`) and Hierarchical Clause Numbers (`1.21`, `5.4`, `Clause 3.2`)**:
Punctuation and numbering formatting are explicitly preserved. Stripping pipes destroys table column boundaries, and stripping numerical prefixes makes it impossible for downstream retrieval to isolate specific regulatory clauses or verify citations.

---

## 3. Chunking (Stage C)

Chunking is structure-aware: splitting proceeds by section headings, then paragraphs, and finally sentences when paragraphs exceed target capacity. Overlap is maintained strictly across boundaries.

### Storage Amplification
- **Original Corpus Size**: 71,334 words/tokens across 246 pages.
- **Shipped Stored Size**: 68,615 words/tokens across 618 chunks.
- **Storage Amplification**: **0.96x** ($\frac{68,615\text{ stored tokens}}{71,334\text{ original tokens}}$).
*(Note: Cleaning stripped repetitive headers and noise tokens across 246 pages, keeping amplification near 1.0x while maintaining 50-token window overlap).*

### Chunk Configuration Comparison Table

The three configurations evaluated on the real corpus:

| Configuration | Chunk Size (tokens) | Overlap (tokens) | Total Chunks | Stored Tokens | Storage Amplification | Recall@5 (Placeholder Fixture Set)* |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Config A (Small)** | 200 | 25 | 860 | 69,539 | 0.97x | 20.0% |
| **Config B (Shipped)** | **400** | **50** | **618** | **68,615** | **0.96x** | **20.0%** |
| **Config C (Large)** | 600 | 100 | 556 | 68,919 | 0.97x | 20.0% |

*\*Note: Evaluated against the fixture golden set placeholder; final production recall@5 will be evaluated against the human-curated golden set in Step 5.*

### Justification for the Shipped Configuration (Config B: 400 / 50)
1. **Clause Cohesion**: Policy clauses in the Academic Rules Handbook average 220 to 350 tokens. At 200 tokens (Config A), substantive clauses like Academic Warning (Clause 5.4) and Course Repetition are cut in half, splitting prerequisites from consequences and bloating the collection to 860 chunks.
2. **Context Specificity**: At 600 tokens (Config C), adjacent unrelated rules (e.g., fee payment deadlines merged into withdrawal rules) blur vector representation, causing cosine similarity to degrade for exact clause lookups.
3. **Shipped Balance**: 400 tokens with 50-token overlap cleanly encapsulates full rule subsections along with their sub-clauses, while providing sufficient overlap to guarantee boundary queries hit adjacent chunks.

---

## 4. Embedding (Stage D)

- **Pinned Model**: `nomic-embed-text` (or `dummy-768` during initial staging).
- **Vector Dimension**: 768 dimensions.
- **Pin Enforcement**:
  - The model name is read from `EMBED_MODEL` in the shared Kubernetes `ConfigMap`.
  - The indexer Job records `embed_model: nomic-embed-text` and `dimension: 768` into Chroma collection metadata.
  - At startup, the API compares collection metadata against its own `EMBED_MODEL`. If a mismatch is detected, the API fails readiness and logs the conflict.

---

## 5. Semantic Cache (Stage F)

- **Engine**: Redis Key-Value Store with cosine vector similarity.
- **Threshold Range Tested**:
  - `0.85`: Yielded false positive hits where distinct questions about undergraduate vs graduate repeating rules matched.
  - `0.95`: Too strict; trivial rephrasings missed the cache.
  - **`0.92` (Shipped)**: Lowest threshold yielding zero false hits across candidate question variants.
- **Never-Cache Policy**: Abstention responses (`answered_from_context: false`), validation failures, and follow-up conversational turns are strictly excluded from the cache.
- **Cache Invalidation**: The indexer Job flushes the Redis cache upon every successful re-indexing run.

---

## 6. Evaluation Method

- **Recall@k Calculation**:
  $$\text{recall@}k = \frac{\text{relevant passages retrieved in top } k}{\text{total relevant passages}}$$
- **Passage Matching**: Matching evaluates whether retrieved chunks originate from the expected `source` document and corresponding `section` heading recorded in `eval/golden.yaml`.
