"""Semantic cache layer using Redis or in-memory vector cosine similarity."""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from pydantic import Field

from ..config import Settings
from ..retrieval.chroma import embed_query
from ..schemas import Answer, Citation, Passage, RetrievedRef

log = logging.getLogger("rahbar.cache")


class CachedAnswer(Answer):
    """Enriched Answer preserving retrieved references across cache hits."""
    retrieved: list[RetrievedRef] = Field(default_factory=list)


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two unit vectors."""
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1)) or 1.0
    norm2 = math.sqrt(sum(b * b for b in v2)) or 1.0
    return max(0.0, min(1.0, dot / (norm1 * norm2)))


class SemanticCache:
    """Semantic cache storing questions, query embeddings, and validated answers."""

    def __init__(self, settings: Settings, redis_client: Any = None) -> None:
        self.settings = settings
        self.threshold = settings.cache_threshold
        self._local_cache: list[dict[str, Any]] = []
        self._redis = redis_client

        if self._redis is None and settings.redis_enabled:
            try:
                import redis

                self._redis = redis.Redis(
                    host=settings.redis_host,
                    port=settings.redis_port,
                    socket_timeout=1.0,
                    decode_responses=True,
                )
            except Exception as exc:
                log.warning("failed to initialize redis client: %s; falling back to in-memory", exc)
                self._redis = None

    def clear_cache(self) -> None:
        """Invalidate the cache on re-index so old answers are never served from new documents."""
        self._local_cache.clear()
        if self._redis is not None:
            try:
                self._redis.flushdb()
                log.info("redis cache invalidated")
            except Exception as exc:
                log.warning("redis flush failed: %s", exc)

    def invalidate(self) -> None:
        """Alias for cache invalidation."""
        self.clear_cache()

    def get(self, question: str) -> CachedAnswer | None:
        """Search cache for a semantic hit exceeding similarity threshold."""
        query_vec = embed_query(question, self.settings)

        # 1. Search in-memory cache
        best_score = 0.0
        best_match = None

        for entry in self._local_cache:
            sim = _cosine_similarity(query_vec, entry["embedding"])
            if sim > best_score:
                best_score = sim
                best_match = entry

        # 2. Check threshold
        if best_match and best_score >= self.threshold:
            log.info("cache hit", extra={"score": round(best_score, 4), "threshold": self.threshold})
            ans_dict = dict(best_match["answer"])
            ans_dict["retrieved"] = best_match.get("retrieved", [])
            return CachedAnswer.model_validate(ans_dict)

        return None

    def set(self, question: str, answer: Answer, retrieved: list[Passage] | None = None) -> None:
        """Store turn in cache. NEVER cache abstentions, out-of-scope, or follow-ups."""
        # Never-cache rules (section 3.3):
        # A cached mistake is served to everyone.
        if not answer.answered_from_context:
            log.debug("skip_cache: refusing to cache abstention")
            return

        if not answer.citations or answer.confidence == "low":
            log.debug("skip_cache: refusing to cache uncertain or uncited answer")
            return

        query_vec = embed_query(question, self.settings)
        refs = [
            RetrievedRef(chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page, text=p.text, score=p.score).model_dump()
            for p in (retrieved or [])
        ]
        entry = {
            "question": question,
            "embedding": query_vec,
            "answer": answer.model_dump(),
            "retrieved": refs,
            "timestamp": time.time(),
        }
        self._local_cache.append(entry)

        if self._redis is not None:
            try:
                key = f"rahbar:cache:{hash(question)}"
                self._redis.set(key, json.dumps(entry), ex=86400)
            except Exception as exc:
                log.warning("redis set failed: %s", exc)


_cache_instance: SemanticCache | None = None


def get_cache(settings: Settings) -> SemanticCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SemanticCache(settings)
    return _cache_instance
