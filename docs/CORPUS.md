# Corpus Documentation: FAST-NUCES Regulations & Policies

This document records the composition, provenance, cryptographic checksums, and architectural failure modes mapped across the RAHBAR corpus.

**Source**: Official FAST-NUCES Public Website (`nu.edu.pk`)  
**Total Documents**: 7 downloaded documents (plus 1 deliberately omitted policy document)  
**Total Pages**: 246 pages across all PDF files (exceeding the 40-page minimum requirement)  
**Verification Command**: `python corpus/fetch.py --verify`

---

## 1. Corpus Property Mapping Table

| # | Required Property | Document(s) Providing It | Evidence & Failure Mode Addressed |
|---|---|---|---|
| **1** | **Scale ($\ge 6$ docs, $\ge 40$ pages)** | All 7 documents in `corpus/MANIFEST.md` | Total 246 PDF pages. Confirmed via `pypdf` page iterator. Sets real multi-document cluster scale. |
| **2** | **Two versions of one policy** | 1. `academic-rules-handbook-jun-2026.pdf` (Current, `doc_version: "2026-06"`, `superseded: false`)<br>2. `undergraduate-academic-rules-aug-2023.pdf` (Superseded, `doc_version: "2023-08"`, `superseded: true`) | **Failure Mode E (Outdated source)**: Prevents retriever from returning repealed 2023 clauses when answering 2026 rule questions. Enforced via metadata filter `where: {"superseded": False}`. |
| **3** | **Scanned, image-only PDF with no text layer** | `faculty-entrepreneurship-and-technical-start-ups-policy.pdf` (8 pages) | **Failure Mode A (Bad extraction)**: `pdffonts` returns empty font list (`fonts=[]`); `extract_text()` returns 0 chars. Requires OCR pipeline via Tesseract (`pdf2image` + `pytesseract`) or explicit loss documentation. |
| **4** | **Document with tables carrying real information** | 1. `annual-report-2023-24.pdf` (85 pages: campus faculty, research publications, degree matrices)<br>2. `grading-criteria.html` (Grade point equivalences, percentage boundaries, CGPA formulas) | **Failure Mode A (Table jumbling)**: Linear text extraction merges tabular columns into illegible streams. Handled via structured table extraction maintaining pipe delimiters (`\|`). |
| **5** | **Document with long definitions** | `student-handbook-jun-2026.pdf` (64 pages: Section XII *Policy on Generative AI Based Tools*) | **Failure Mode B (Poor chunks)**: Nested multi-paragraph regulatory definitions cut in half by naive fixed-size chunkers. Addressed via structure-aware section and paragraph boundary splitting. |
| **6** | **Realistic deliberately absent topic** | **Hostel & Transport Accommodation Policy**<br>*(Deliberately omitted `Transport and Hostel Facilities.pdf` from `corpus/sources.yaml`)* | **Abstention Integrity (Section 3.4)**: Plausible student query (hostel curfew, room allocation criteria, mess security deposit refund). The `Student Handbook` mentions hostels exist in passing, but specific allocation rules are deliberately absent. The system must decline with `answered_from_context: false`. |
| **7** | **Exact-term content (clauses, codes, CGPA, fees)** | `academic-rules-handbook-jun-2026.pdf` (Clause 1.21 attendance, Clause 5.4 warning, CGPA 2.00 threshold)<br>`grading-criteria.html` (B+ = 3.33)<br>`prospectus-2026-academic-rules-and-regulations.pdf` | **Failure Mode C (Embedding semantic blur)**: Dense numerical thresholds and clause identifiers get blurred in vector space, motivating hybrid retrieval and explicit threshold testing. |

---

## 2. Document Inventory and Cryptographic Manifest

All documents were downloaded using polite rate-limiting ($2.0\text{s}$ pause between requests) via `corpus/fetch.py` and recorded in `corpus/MANIFEST.md`:

1. **Academic Rules Handbook (Jun 2026)**
   - File: `academic-rules-handbook-jun-2026.pdf`
   - Size: 987,731 bytes | 58 pages
   - SHA-256: `4cfee3e191b84aeb9168f17656c7642212ff52b90f2780d028b33c154aa74b62`
   - Role: Current primary academic regulations.
2. **Undergraduate Academic Rules (Aug 2023)**
   - File: `undergraduate-academic-rules-aug-2023.pdf`
   - Size: 14,888,226 bytes | 26 pages
   - SHA-256: `cddfe1f5e9770132e27d686abd1c2e6742ef4732f0c2f431ed514bc1691d2004`
   - Role: Superseded historical rules for version comparison.
3. **Prospectus 2026 - Academic Rules and Regulations**
   - File: `prospectus-2026-academic-rules-and-regulations.pdf`
   - Size: 7,066,767 bytes | 5 pages
   - SHA-256: `a7e7f6896a59eb9413c9e8826e6c4c018ebe52045d10cebfcba92ded10a3174a`
   - Role: Parallel summary prospectus rules (attendance clause 1 vs handbook 1.21).
4. **Faculty Entrepreneurship and Technical Start-ups Policy**
   - File: `faculty-entrepreneurship-and-technical-start-ups-policy.pdf`
   - Size: 3,872,809 bytes | 8 pages
   - SHA-256: `c2d5c7648d1593daf34d5c0596ede476e8984b0551830cc1690017e6e22f1359`
   - Role: Verified pure-image scanned document for OCR pipeline validation.
5. **Annual Report 2023-24**
   - File: `annual-report-2023-24.pdf`
   - Size: 5,100,456 bytes | 85 pages
   - SHA-256: `3c9d89e84af24df4b939f10e32c620a3877b276acea1db52f1aae4098e5c057a`
   - Role: Multi-column tables, statistical graduate data.
6. **Grading Criteria**
   - File: `grading-criteria.html`
   - Size: 95,673 bytes | 1 web page
   - SHA-256: `66a995b4d8474e914d37bfa14755a5361f47327d5a7314f9b7ec15d9b6176e6e`
   - Role: HTML tabular matrices for grade point thresholds.
7. **Student Handbook (Jun 2026)**
   - File: `student-handbook-jun-2026.pdf`
   - Size: 1,980,275 bytes | 64 pages
   - SHA-256: `717eb4b8d37e50185ffe952735c08ef19edd16074d547f10ebd22cf23da67c4d`
   - Role: Long administrative definitions, Discipline Committee powers, GenAI policy.
