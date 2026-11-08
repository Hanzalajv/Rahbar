"""Stage 4: Embed. One vector per chunk, from the pinned model only."""

from __future__ import annotations

import httpx

from .config import IndexerSettings

DEFAULT_DIMENSION = 768


def embed(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Embed chunks using the pinned settings.embed_model.

    Calls Ollama /api/embed in batches of 32. Fails loudly if Ollama is unreachable.
    """
    if not texts:
        return []

    # Pin verification
    embed_model = settings.embed_model
    url = f"{settings.ollama_url.rstrip('/')}/api/embed"

    batch_size = 32
    all_vectors: list[list[float]] = []

    try:
        with httpx.Client(timeout=httpx.Timeout(45.0, connect=5.0)) as client:
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                payload = {"model": embed_model, "input": batch}
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings") or []
                if len(embeddings) != len(batch):
                    raise RuntimeError(f"Expected {len(batch)} vectors, got {len(embeddings)}")
                all_vectors.extend(embeddings)
        return all_vectors
    except Exception as exc:
        raise RuntimeError(
            f"Ollama embedding unavailable at {url} for model '{embed_model}': {exc}. "
            "Refusing to write hash vectors — fix Ollama connectivity and re-run the Job."
        ) from exc
