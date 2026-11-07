"""Unit tests for the 5-stage indexer pipeline."""

from __future__ import annotations

from pathlib import Path

from pipeline.chunk import chunk
from pipeline.clean import clean
from pipeline.config import IndexerSettings
from pipeline.embed import embed
from pipeline.extract import extract
from pipeline.models import Document, Page
from pipeline.store import store


class FakeCollection:
    def __init__(self, name: str, metadata: dict) -> None:
        self.name = name
        self.metadata = metadata
        self.rows: dict[str, tuple[str, list[float], dict]] = {}

    def add(self, ids, documents, embeddings, metadatas) -> None:
        for i, d, e, m in zip(ids, documents, embeddings, metadatas):
            self.rows[i] = (d, e, m)

    def count(self) -> int:
        return len(self.rows)


class FakeChromaClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def delete_collection(self, name: str) -> None:
        if name in self.collections:
            del self.collections[name]

    def create_collection(self, name: str, metadata: dict) -> FakeCollection:
        self.collections[name] = FakeCollection(name, metadata)
        return self.collections[name]


def test_extract_markdown_fixture() -> None:
    fixture_path = Path(__file__).resolve().parents[2] / "eval" / "fixtures" / "academic-rules-2026.md"
    doc = extract(str(fixture_path), doc_version="2026", superseded=False)
    assert doc.source == "academic-rules-2026.md"
    assert doc.doc_version == "2026"
    assert not doc.superseded
    assert len(doc.pages) >= 1
    assert "Academic Rules" in doc.pages[0].text


def test_clean_preserves_table_and_sections() -> None:
    raw_text = "Some text-\nwith hyphenation.\n\n\n| Course | Grade |\n| CS101 | A |\n\n1.21 Attendance Policy\nAll students must attend."
    doc = Document(source="test.md", doc_version="2026", superseded=False, pages=[Page(source="test.md", page=1, text=raw_text)])
    cleaned = clean(doc)
    text = cleaned.pages[0].text
    assert "textwith" in text  # hyphenation resolved
    assert "| Course | Grade |" in text  # table preserved
    assert "1.21 Attendance Policy" in text  # clause heading preserved


def test_chunking_structure_aware_and_overlap() -> None:
    raw = (
        "1.1 Introduction\nFirst sentence of intro. Second sentence of intro.\n\n"
        "1.2 Grading\nGrade A is 4.0. Grade B is 3.0. Grade C is 2.0."
    )
    doc = Document(source="policy.pdf", doc_version="2026", superseded=False, pages=[Page(source="policy.pdf", page=1, text=raw)])
    chunks = chunk(doc, size=15, overlap=5)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.source == "policy.pdf"
        assert c.page == 1
        assert c.doc_version == "2026"
        assert not c.superseded
        assert c.section in {"1.1 Introduction", "1.2 Grading", "policy.pdf p.1"}


def test_embed_offline_fallback() -> None:
    settings = IndexerSettings(
        embed_model="nomic-embed-text",
        chroma_host="chroma",
        chroma_port=8000,
        collection="rahbar",
        corpus_dir="/corpus",
        chunk_size=400,
        chunk_overlap=50,
        ollama_url="http://127.0.0.1:9999",  # unreachable port
    )
    vectors = embed(["chunk one", "chunk two"], settings)
    assert len(vectors) == 2
    assert len(vectors[0]) == 768
    assert len(vectors[1]) == 768


def test_store_writes_collection_metadata_and_chunk_metadata() -> None:
    settings = IndexerSettings(
        embed_model="nomic-embed-text",
        chroma_host="memory",
        chroma_port=8000,
        collection="rahbar_test",
        corpus_dir="/corpus",
        chunk_size=400,
        chunk_overlap=50,
        ollama_url="http://127.0.0.1:9999",
    )
    fixture_path = Path(__file__).resolve().parents[2] / "eval" / "fixtures" / "fees-2026.md"
    doc = clean(extract(str(fixture_path), doc_version="2026", superseded=False))
    chunks = chunk(doc, size=50, overlap=10)
    vectors = embed([c.text for c in chunks], settings)

    client = FakeChromaClient()
    count = store(chunks, vectors, settings, client=client)

    assert count == len(chunks)
    col = client.collections["rahbar_test"]
    assert col.metadata["embed_model"] == "nomic-embed-text"
    assert col.metadata["dimension"] == 768
    for _doc, vec, meta in col.rows.values():
        assert len(vec) == 768
        assert "source" in meta
        assert "section" in meta
        assert "page" in meta
        assert "doc_version" in meta
        assert "superseded" in meta
