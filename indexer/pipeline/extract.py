"""Stage 1: Extract. PDF (or HTML, or Markdown) to text, page by page."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .models import Document, Page

# Regular expressions for detecting structural markers
HEADING_REGEX = re.compile(r"^(#{1,6}\s+|(?:\d+\.)+\d*\s+|[A-Z\s]{4,}:?$)", re.M)


class _HTMLToText(HTMLParser):
    """HTML parser converting HTML to structured text while preserving tables and headings."""

    def __init__(self) -> None:
        super().__init__()
        self.output: list[str] = []
        self._in_table = False
        self._current_row: list[str] = []
        self._in_cell = False
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.output.append(f"\n\n## ")
        elif tag in {"p", "div", "br", "li"}:
            self.output.append("\n")
        elif tag == "table":
            self._in_table = True
            self.output.append("\n\n")
        elif tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"}:
            self._in_cell = True
            self._cell_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self._in_cell = False
            self._current_row.append(" ".join("".join(self._cell_text).split()))
        elif tag == "tr":
            if self._current_row:
                self.output.append("| " + " | ".join(self._current_row) + " |\n")
        elif tag == "table":
            self._in_table = False
            self.output.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)
        else:
            self.output.append(data)

    def get_text(self) -> str:
        return "".join(self.output).strip()


def _strip_repeating_headers_footers(pages_text: list[str]) -> list[str]:
    """Identify lines appearing in header/footer zones across >=60% of pages and strip them."""
    if len(pages_text) <= 2:
        return pages_text

    top_lines: dict[str, int] = {}
    bottom_lines: dict[str, int] = {}

    for text in pages_text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) >= 3:
            for ln in lines[:2]:
                # Don't treat section headings as headers
                if not re.match(r"^\d+\.\d+", ln):
                    top_lines[ln] = top_lines.get(ln, 0) + 1
            for ln in lines[-2:]:
                if not re.match(r"^\d+\.\d+", ln):
                    bottom_lines[ln] = bottom_lines.get(ln, 0) + 1

    threshold = max(2, int(len(pages_text) * 0.6))
    headers_to_strip = {ln for ln, count in top_lines.items() if count >= threshold}
    footers_to_strip = {ln for ln, count in bottom_lines.items() if count >= threshold}
    strip_set = headers_to_strip | footers_to_strip

    if not strip_set:
        return pages_text

    cleaned_pages = []
    for text in pages_text:
        lines = text.splitlines()
        filtered = [ln for ln in lines if ln.strip() not in strip_set]
        cleaned_pages.append("\n".join(filtered))
    return cleaned_pages


def _try_ocr_page(pdf_path: str, page_number: int) -> str:
    """Attempt OCR using pytesseract and pdf2image if system binaries exist."""
    try:
        import pytesseract
        from pdf2image import convert_from_path

        # Convert specific page (1-based)
        images = convert_from_path(pdf_path, first_page=page_number, last_page=page_number)
        if images:
            text = pytesseract.image_to_string(images[0])
            return text.strip()
    except Exception:
        pass
    return ""


def extract(path: str, doc_version: str, superseded: bool) -> Document:
    """Return the document's text page by page.

    Detects pages with no text layer and OCRs them; marks those pages ocr=True.
    """
    file_path = Path(path)
    source = file_path.name
    suffix = file_path.suffix.lower()

    # Case 1: Markdown (.md)
    if suffix == ".md":
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        # Strip front-matter if present
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            raw = parts[2] if len(parts) >= 3 else raw
        return Document(source=source, doc_version=doc_version, superseded=superseded,
                        pages=[Page(source=source, page=1, text=raw.strip(), ocr=False)])

    # Case 2: HTML (.html, .htm)
    if suffix in {".html", ".htm"}:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        parser = _HTMLToText()
        parser.feed(raw)
        return Document(source=source, doc_version=doc_version, superseded=superseded,
                        pages=[Page(source=source, page=1, text=parser.get_text(), ocr=False)])

    # Case 3: Plain text (.txt)
    if suffix == ".txt":
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        return Document(source=source, doc_version=doc_version, superseded=superseded,
                        pages=[Page(source=source, page=1, text=raw.strip(), ocr=False)])

    # Case 4: PDF (.pdf)
    import pypdf

    reader = pypdf.PdfReader(str(file_path))
    raw_pages: list[tuple[int, str, bool]] = []

    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        # Check if the page lacks extractable text layer
        clean_text = text.strip()
        is_scanned = len(clean_text) < 20

        if is_scanned:
            ocr_text = _try_ocr_page(str(file_path), i)
            if ocr_text:
                raw_pages.append((i, ocr_text, True))
            else:
                # Documented loss when system OCR toolchain is absent on host
                raw_pages.append((i, clean_text or "[Scanned page: image-only without text layer]", True))
        else:
            raw_pages.append((i, clean_text, False))

    # Strip repeating headers and footers across pages
    extracted_texts = [p[1] for p in raw_pages]
    stripped_texts = _strip_repeating_headers_footers(extracted_texts)

    pages = [
        Page(source=source, page=raw_pages[i][0], text=stripped_texts[i], ocr=raw_pages[i][2])
        for i in range(len(raw_pages))
    ]

    return Document(source=source, doc_version=doc_version, superseded=superseded, pages=pages)
