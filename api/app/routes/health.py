"""Liveness and readiness (section 4.3). They answer different questions."""

from __future__ import annotations

import logging
from fastapi import APIRouter, Request, Response

router = APIRouter()
log = logging.getLogger("rahbar.health")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Is the process alive? Must never touch Chroma, Redis or the LLM."""
    return {"status": "alive"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, object]:
    """Is the service ready to accept user traffic?

    Returns 200 only if Chroma is reachable, the collection is non-empty,
    Redis (if configured) is reachable, and the collection's embedding model matches EMBED_MODEL.
    Otherwise sets status_code=503 naming the failed dependency.
    """
    app = request.app
    settings = getattr(app.state, "settings", None)
    if settings is None:
        return {"status": "ready", "checks": {"mode": "booting"}}

    # If running with stub retriever in offline tests or dev mode
    if settings.retriever == "stub":
        return {"status": "ready", "checks": {"retriever": "stub"}}

    # Check for startup embedding pin error
    pin_error = getattr(app.state, "pin_error", None)
    if pin_error:
        response.status_code = 503
        return {
            "status": "not_ready",
            "error": "embedding_model mismatch",
            "detail": pin_error,
        }

    checks: dict[str, object] = {}

    # Check Chroma reachability and non-empty collection
    try:
        retriever = getattr(app.state, "retriever", None)
        if retriever is None or not hasattr(retriever, "client"):
            response.status_code = 503
            return {"status": "not_ready", "failed_dependency": "chroma", "error": "Retriever not initialized"}

        collection = retriever.client.get_collection(settings.collection)
        count = collection.count()

        if count == 0:
            response.status_code = 503
            return {
                "status": "not_ready",
                "failed_dependency": "chroma",
                "error": f"Collection '{settings.collection}' is empty (count: 0)",
            }
        checks["chroma"] = "ok"
        checks["chunks_indexed"] = count

        # Verify embedding model metadata pin
        col_meta = collection.metadata or {}
        col_model = col_meta.get("embed_model")
        if col_model and col_model != settings.embed_model:
            response.status_code = 503
            return {
                "status": "not_ready",
                "failed_dependency": "embedding_pin",
                "error": f"embedding_model mismatch: collection uses '{col_model}', API configured with '{settings.embed_model}'",
            }
        checks["embedding_pin"] = "match"
    except Exception as exc:
        response.status_code = 503
        return {"status": "not_ready", "failed_dependency": "chroma", "error": str(exc)}

    # Check Redis cache reachability if enabled
    if settings.redis_enabled:
        try:
            import redis

            r = redis.Redis(host=settings.redis_host, port=settings.redis_port, socket_timeout=1.0)
            if not r.ping():
                raise RuntimeError("Redis ping returned false")
            checks["redis"] = "ok"
        except Exception as exc:
            response.status_code = 503
            return {"status": "not_ready", "failed_dependency": "redis", "error": str(exc)}

    return {"status": "ready", "checks": checks}
