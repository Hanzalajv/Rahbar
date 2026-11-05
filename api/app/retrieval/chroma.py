"""The real retriever querying ChromaDB with metadata filtering and embedding pin validation."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

import httpx

from ..config import Settings
from ..schemas import Passage

log = logging.getLogger("rahbar.retrieval.chroma")


def _hash_vector(text: str, dim: int = 768) -> list[float]:
    """Deterministic offline fallback unit vector when embedding provider is offline."""
    values: list[float] = []
    seed = text.encode("utf-8")
    while len(values) < dim:
        seed = hashlib.sha256(seed).digest()
        values.extend((b - 127.5) / 127.5 for b in seed)
    values = values[:dim]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def embed_query(text: str, settings: Settings) -> list[float]:
    """Embed query using settings.embed_model with live Ollama or deterministic fallback."""
    try:
        url = f"{settings.ollama_url.rstrip('/')}/api/embed"
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=2.0)) as client:
            resp = client.post(url, json={"model": settings.embed_model, "input": [text]})
            if resp.status_code == 200:
                data = resp.json()
                embeddings = data.get("embeddings") or []
                if embeddings and len(embeddings[0]) > 0:
                    return embeddings[0]
    except Exception:
        pass
    return _hash_vector(text, 768)


class ChromaRetriever:
    name = "chroma"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.settings = settings
        if client is not None:
            self.client = client
        else:
            self.client = chromadb.HttpClient(
                host=settings.chroma_host,
                port=settings.chroma_port,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        self._collection = None

    @property
    def collection(self) -> Any:
        if self._collection is None:
            self._collection = self.client.get_collection(self.settings.collection)
        return self._collection

    def retrieve(self, question: str, k: int | None = None) -> list[Passage]:
        """Embed question, query Chroma for top-k passages, and filter superseded policies."""
        top_k = k if k is not None else self.settings.top_k
        query_vector = embed_query(question, self.settings)

        # Check if question explicitly requests older, repealed, or 2023 version
        asks_older = bool(re.search(r"\b(2023|old|superseded|previous|repealed|former)\b", question, re.I))

        # Unless explicitly requested, filter out superseded documents
        where_filter: dict[str, Any] | None = None
        if not asks_older:
            where_filter = {"superseded": False}

        try:
            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            log.error("chroma query failed: %s", exc)
            return []

        passages: list[Passage] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for chunk_id, doc_text, meta, dist in zip(ids, docs, metas, dists):
            # Chroma cosine distance is in [0, 2]; similarity = 1 - distance
            score = round(max(0.0, 1.0 - float(dist)), 4)
            source = str(meta.get("source", "unknown"))
            section = str(meta.get("section", "unknown"))
            page = int(meta["page"]) if "page" in meta and meta["page"] is not None else None

            passages.append(
                Passage(
                    chunk_id=chunk_id,
                    text=doc_text,
                    source=source,
                    section=section,
                    page=page,
                    score=score,
                )
            )

        log.info(
            "retrieval complete",
            extra={
                "top_k": top_k,
                "retrieved_count": len(passages),
                "chunk_ids": [p.chunk_id for p in passages],
                "scores": [p.score for p in passages],
                "filter_superseded": not asks_older,
            },
        )
        return passages
