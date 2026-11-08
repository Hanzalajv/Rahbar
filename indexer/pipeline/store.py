"""Stage 5: Store. The only code in the whole system that writes to Chroma."""

from __future__ import annotations

import os
from typing import Any

from .config import IndexerSettings
from .models import Chunk


def get_client(settings: IndexerSettings) -> Any:
    """Instantiate ChromaDB client based on configuration with persistent local fallback."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    host = getattr(settings, "chroma_host", "")
    if host in ("local", "embedded", "persistent") or os.environ.get("CHROMA_PERSIST_DIR"):
        persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "./chroma_data")
        return chromadb.PersistentClient(path=persist_dir)

    try:
        client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        client.heartbeat()
        return client
    except Exception:
        persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "./chroma_data")
        return chromadb.PersistentClient(path=persist_dir)


def store(chunks: list[Chunk], vectors: list[list[float]], settings: IndexerSettings, client: Any = None) -> int:
    """Write every chunk with its vector and metadata, and record the embedding model
    and vector dimension in the collection metadata (section 4.4). Return the count."""
    if not chunks:
        return 0

    if not vectors or len(vectors) != len(chunks):
        raise ValueError(
            f"Vector count ({len(vectors) if vectors else 0}) does not match chunk count ({len(chunks)})"
        )

    dimension = len(vectors[0])
    if dimension != 768:
        raise RuntimeError(f"Vector dimension {dimension} != 768 for model '{settings.embed_model}'")

    if client is None:
        client = get_client(settings)

    embed_model = settings.embed_model

    collection_metadata = {
        "hnsw:space": "cosine",
        "embed_model": embed_model,
        "dimension": dimension,
    }

    # Recreate collection to prevent stale or orphaned chunks
    try:
        client.delete_collection(settings.collection)
    except Exception:
        pass

    collection = client.create_collection(
        name=settings.collection,
        metadata=collection_metadata,
    )

    ids = [c.id for c in chunks]
    documents = [c.text for c in chunks]
    metadatas = [
        {
            "source": c.source,
            "section": c.section,
            "page": c.page,
            "doc_version": c.doc_version,
            "superseded": c.superseded,
        }
        for c in chunks
    ]

    # Batch inserts into Chroma (max 250 per batch)
    batch_size = 250
    for i in range(0, len(chunks), batch_size):
        end = i + batch_size
        collection.add(
            ids=ids[i:end],
            documents=documents[i:end],
            embeddings=vectors[i:end],
            metadatas=metadatas[i:end],
        )

    return int(collection.count())
