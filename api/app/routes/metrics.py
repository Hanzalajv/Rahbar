"""Prometheus metrics at /metrics (section 4.8).

Exposes: request count and latency, retrieval latency, generation latency,
cache hits and misses, abstentions, fallbacks, and token estimates.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

router = APIRouter()

REQUESTS = Counter("rahbar_requests_total", "HTTP requests", ["path", "status"])
LATENCY = Histogram(
    "rahbar_request_seconds",
    "HTTP request latency",
    ["path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)

RETRIEVAL_LATENCY = Histogram(
    "rahbar_retrieval_seconds",
    "Retrieval latency from vector database",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
GENERATION_LATENCY = Histogram(
    "rahbar_generation_seconds",
    "LLM answer generation latency",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60),
)

CACHE_HITS = Counter("rahbar_cache_hits_total", "Semantic cache hits")
CACHE_MISSES = Counter("rahbar_cache_misses_total", "Semantic cache misses")
ABSTENTIONS = Counter("rahbar_abstentions_total", "Questions declined with answered_from_context: false")
FALLBACKS = Counter("rahbar_fallbacks_total", "Safe fallback responses served on validation errors")
TOKENS_IN = Counter("rahbar_tokens_in_total", "Estimated prompt tokens sent to generation")
TOKENS_OUT = Counter("rahbar_tokens_out_total", "Estimated response tokens generated")


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
