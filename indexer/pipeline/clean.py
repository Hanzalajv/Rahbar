"""Stage 2: Clean. Remove artefacts without destroying structure."""

from __future__ import annotations

import re

from .models import Document, Page

# Soft hyphen and common control characters (excluding tab and newline)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Line break hyphenation: e.g. "academ-\nic" -> "academic"
HYPHENATION_RE = re.compile(r"(\w+)-\n(\w+)")
# Excessive blank lines: more than 2 consecutive newlines collapsed to 2
EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")
# Trailing whitespace on lines
TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.M)


def clean_text(text: str) -> str:
    """Clean text artefacts while strictly preserving structural tokens.

    DELIBERATELY NOT STRIPPED:
    1. Table formatting characters (`|` pipe delimiters and `-` hyphens used in markdown tables).
       Stripping them destroys tabular column boundaries required for fee structures and grading scales.
    2. Numbered clause prefixes (e.g., `1.21`, `5.4`, `Clause 3.2`) and section headings.
       These are vital for structure-aware chunking and exact citation attribution.
    """
    if not text:
        return ""

    # Remove non-printable control characters
    text = CONTROL_CHAR_RE.sub("", text)

    # Rejoin words broken across line-breaks by hyphens
    text = HYPHENATION_RE.sub(r"\1\2", text)

    # Trim trailing whitespace per line
    text = TRAILING_SPACE_RE.sub("", text)

    # Normalize excessive consecutive newlines to paragraph breaks
    text = EXCESS_NEWLINES_RE.sub("\n\n", text)

    return text.strip()


def clean(doc: Document) -> Document:
    """Strip noise and normalization artefacts from every page while keeping headings and structure."""
    cleaned_pages: list[Page] = []
    for p in doc.pages:
        c_text = clean_text(p.text)
        cleaned_pages.append(Page(source=p.source, page=p.page, text=c_text, ocr=p.ocr))
    return Document(source=doc.source, doc_version=doc.doc_version, superseded=doc.superseded, pages=cleaned_pages)
