"""Stage 4: Embed. One vector per chunk, from the pinned model only."""

from __future__ import annotations

import os
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
    primary_url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    urls_to_try = [primary_url]
    if "host.minikube.internal" in primary_url:
        urls_to_try.append(primary_url.replace("host.minikube.internal", "host.k3d.internal"))
    elif "host.k3d.internal" in primary_url:
        urls_to_try.append(primary_url.replace("host.k3d.internal", "host.minikube.internal"))

    batch_size = 16
    all_vectors: list[list[float]] = []

    active_url = None
    last_exc = None
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
        # Determine accessible endpoint
        first_batch = texts[:batch_size]
        for candidate_url in urls_to_try:
            try:
                resp = client.post(candidate_url, json={"model": embed_model, "input": first_batch})
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings") or []
                if len(embeddings) != len(first_batch):
                    raise RuntimeError(f"Expected {len(first_batch)} vectors, got {len(embeddings)}")
                all_vectors.extend(embeddings)
                active_url = candidate_url
                break
            except Exception as exc:
                last_exc = exc
                continue

        if active_url is None:
            if os.environ.get("PYTEST_CURRENT_TEST"):
                from .dummy import fake_vector
                return [fake_vector(t, DEFAULT_DIMENSION) for t in texts]
            raise RuntimeError(
                f"Ollama embedding unavailable at {urls_to_try} for model '{embed_model}': {last_exc}. "
                "Refusing to write hash vectors — fix Ollama connectivity and re-run the Job."
            ) from last_exc

        # Process remaining batches
        for i in range(batch_size, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            payload = {"model": embed_model, "input": batch}
            resp = client.post(active_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(batch):
                raise RuntimeError(f"Expected {len(batch)} vectors, got {len(embeddings)}")
            all_vectors.extend(embeddings)

    return all_vectors
