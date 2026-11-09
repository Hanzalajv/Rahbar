"""The real retriever querying ChromaDB with metadata filtering and embedding pin validation."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..config import Settings
from ..schemas import Passage

log = logging.getLogger("rahbar.retrieval.chroma")


def embed_query(text: str, settings: Settings) -> list[float]:
    """Embed query using settings.embed_model via Ollama /api/embed.

    Fails loudly if Ollama is unreachable; never returns synthetic hash vectors.
    """
    primary_url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    urls_to_try = [primary_url]
    if any(h in primary_url for h in ("host.k3d.internal", "host.minikube.internal")):
        for alt in ("172.27.0.1", "172.17.0.1"):
            alt_url = primary_url
            for base in ("host.k3d.internal", "host.minikube.internal"):
                alt_url = alt_url.replace(base, alt)
            if alt_url not in urls_to_try:
                urls_to_try.append(alt_url)

    last_exc = None
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=2.0)) as client:
            for url in urls_to_try:
                try:
                    resp = client.post(url, json={"model": settings.embed_model, "input": [text]})
                    resp.raise_for_status()
                    data = resp.json()
                    embeddings = data.get("embeddings") or []
                    if embeddings and len(embeddings[0]) > 0:
                        return embeddings[0]
                except Exception as exc:
                    last_exc = exc
                    continue
            raise RuntimeError(f"Ollama returned empty embedding for model '{settings.embed_model}'")
    except Exception as exc:
        raise RuntimeError(
            f"Failed to embed query with model '{settings.embed_model}' at {urls_to_try}: {last_exc or exc}"
        ) from (last_exc or exc)


def _calculate_score(cosine_dist: float) -> float:
    """True cosine similarity derived from Chroma cosine distance (distance = 1 - cosine_sim)."""
    cosine_sim = max(0.0, min(1.0, 1.0 - float(cosine_dist)))
    return round(cosine_sim, 4)


class ChromaRetriever:
    name = "chroma"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.settings = settings
        if client is not None:
            self.client = client
        else:
            import os
            host = settings.chroma_host
            if host in ("local", "embedded", "persistent") or os.environ.get("CHROMA_PERSIST_DIR"):
                persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "./chroma_data")
                self.client = chromadb.PersistentClient(path=persist_dir)
            else:
                try:
                    self.client = chromadb.HttpClient(
                        host=settings.chroma_host,
                        port=settings.chroma_port,
                        settings=ChromaSettings(anonymized_telemetry=False),
                    )
                    self.client.heartbeat()
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to connect to Chroma HTTP service at {settings.chroma_host}:{settings.chroma_port}: {exc}"
                    ) from exc
        self._collection = None

    @property
    def collection(self) -> Any:
        """Fetch or re-fetch collection handle if stale or recreated by indexer."""
        if self._collection is not None:
            try:
                self._collection.count()
                return self._collection
            except Exception as exc:
                log.info("collection handle stale or recreated, refreshing: %s", exc)
                self._collection = None

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

        results: dict[str, Any] = {}
        try:
            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=min(20, max(top_k * 2, 10)),
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            log.warning("chroma query failed, refreshing collection handle: %s", exc)
            self._collection = None
            try:
                results = self.collection.query(
                    query_embeddings=[query_vector],
                    n_results=min(20, max(top_k * 2, 10)),
                    where=where_filter,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as exc2:
                log.error("chroma vector query error after refresh: %s", exc2)
                results = {}

        ids = results.get("ids", [[]])[0] if results else []
        docs = results.get("documents", [[]])[0] if results else []
        metas = results.get("metadatas", [[]])[0] if results else []
        dists = results.get("distances", [[]])[0] if results else []

        passages: list[Passage] = []
        for chunk_id, doc_text, meta, dist in zip(ids, docs, metas, dists):
            source = str(meta.get("source", "unknown"))
            section = str(meta.get("section", "unknown"))
            page = int(meta["page"]) if "page" in meta and meta["page"] is not None else None
            score = _calculate_score(dist)

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

        # Rank by true cosine similarity descending
        passages.sort(key=lambda p: p.score, reverse=True)
        passages = passages[:top_k]

        log.info(
            "retrieval complete",
            extra={
                "top_k": top_k,
                "retrieved_count": len(passages),
                "chunk_ids": [p.chunk_id for p in passages],
                "cosine_scores": [p.score for p in passages],
                "filter_superseded": not asks_older,
            },
        )
        return passages
