"""Stage 3: Chunk. Structure-aware, with overlap."""

from __future__ import annotations

import re
from typing import Iterator

from .models import Chunk, Document

# Detect section headings like "# Heading", "1.21 Heading", "Clause 4.2", "SECTION 1"
SECTION_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+(.+)|((?:Clause\s+)?\d+(?:\.\d+)*\s+[A-Z][^\n]+)|([A-Z\s]{4,}:))$",
    re.M,
)

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _approx_token_count(text: str) -> int:
    """Approximate token count based on whitespace word splits (roughly 1.3 tokens/word)."""
    words = text.split()
    return max(1, int(len(words) * 1.3))


def _split_into_sentences(paragraph: str) -> list[str]:
    """Split paragraph into sentences, preserving sentence endings."""
    sentences = SENTENCE_SPLIT_RE.split(paragraph.strip())
    return [s.strip() for s in sentences if s.strip()]


def _extract_sections(text: str, default_section: str) -> list[tuple[str, str]]:
    """Split text into (section_heading, section_body) pairs."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_heading = default_section
    current_lines: list[str] = []

    for line in lines:
        match = SECTION_HEADING_RE.match(line.strip())
        if match:
            if current_lines:
                sections.append((current_heading, current_lines))
                current_lines = []
            heading_text = match.group(1) or match.group(2) or match.group(3) or line.strip()
            current_heading = heading_text.strip("# ").strip()
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_heading, current_lines))

    return [(heading, "\n".join(body_lines).strip()) for heading, body_lines in sections if "\n".join(body_lines).strip()]


def chunk_text(doc: Document, size: int, overlap: int) -> list[Chunk]:
    """Structure-aware chunking: splits by section, then paragraph, then sentence under target size with overlap.

    Args:
        doc: Cleaned Document.
        size: Target chunk size in approximate tokens.
        overlap: Token overlap between adjacent chunks.

    Returns:
        List of Chunks with complete metadata.
    """
    chunks: list[Chunk] = []
    chunk_counter = 0

    clean_source = re.sub(r"[^a-z0-9]+", "-", doc.source.lower()).strip("-")[:40]

    for page in doc.pages:
        page_text = page.text.strip()
        if not page_text:
            continue

        default_sec = f"{doc.source} p.{page.page}"
        sections = _extract_sections(page_text, default_section=default_sec)

        for section_name, section_body in sections:
            paragraphs = [p.strip() for p in section_body.split("\n\n") if p.strip()]

            # Build unit segments (paragraphs or sentences for oversized paragraphs)
            units: list[str] = []
            for para in paragraphs:
                if _approx_token_count(para) > size:
                    units.extend(_split_into_sentences(para))
                else:
                    units.append(para)

            # Accumulate units into chunks with overlap
            current_units: list[str] = []
            current_tokens = 0

            for unit in units:
                unit_tokens = _approx_token_count(unit)

                if current_tokens + unit_tokens > size and current_units:
                    chunk_text_str = "\n\n".join(current_units)
                    chunk_counter += 1
                    chunk_id = f"{clean_source}-p{page.page:03d}-c{chunk_counter:04d}"
                    chunks.append(
                        Chunk(
                            id=chunk_id,
                            text=chunk_text_str,
                            source=doc.source,
                            section=section_name,
                            page=page.page,
                            doc_version=doc.doc_version,
                            superseded=doc.superseded,
                        )
                    )

                    # Retain overlap from end of current_units
                    overlap_units: list[str] = []
                    overlap_count = 0
                    for u in reversed(current_units):
                        u_tok = _approx_token_count(u)
                        if overlap_count + u_tok <= overlap:
                            overlap_units.insert(0, u)
                            overlap_count += u_tok
                        else:
                            break
                    current_units = overlap_units
                    current_tokens = sum(_approx_token_count(u) for u in current_units)

                current_units.append(unit)
                current_tokens += unit_tokens

            if current_units:
                chunk_text_str = "\n\n".join(current_units)
                chunk_counter += 1
                chunk_id = f"{clean_source}-p{page.page:03d}-c{chunk_counter:04d}"
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        text=chunk_text_str,
                        source=doc.source,
                        section=section_name,
                        page=page.page,
                        doc_version=doc.doc_version,
                        superseded=doc.superseded,
                    )
                )

    return chunks


def chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
    """Standard stage entry point."""
    return chunk_text(doc, size, overlap)
