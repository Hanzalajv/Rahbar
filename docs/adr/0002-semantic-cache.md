# ADR 0002: Dedicated Key-Value Cache (Redis) vs Secondary Vector Collection for Semantic Caching

## Status
Accepted · November 2026

## Context
Production LLM generation incurs significant financial cost, memory pressure, and latency (1–5 seconds per generation). Repeated or semantically equivalent questions (e.g., "What is the attendance threshold?" vs "How much attendance do I need for exams?") should be resolved instantly from cache.
We evaluated whether to implement semantic caching using a dedicated in-memory key-value store with vector similarity (Redis) or a secondary collection within our existing ChromaDB instance.

## Decision
We select **Redis** as the semantic cache storage engine, backed by a persistent volume claim (`redis-pvc`):
1. **Separation of Concerns and Lifecycles**: Document knowledge (authoritative university policy) is relatively static and strictly curated, whereas user interaction turns and semantic query caches are volatile, high-frequency, and transient. Storing user questions in Chroma creates schema clutter and complicates backup/restore operations for authoritative regulatory text.
2. **Sub-millisecond TTL and Eviction**: Redis natively supports time-to-live (`EXPIRE` / TTL) and LRU memory eviction. Chroma collections lack native TTL per document, requiring complex manual cron routines to sweep old cached interactions.
3. **Instant Atomic Cache Invalidation**: When the indexer Job updates policy documents, old cached answers must be instantly evicted to prevent stale answers. In Redis, `flushdb()` or prefix pattern deletion executes in sub-millisecond time.
4. **Never-Cache Governance**: We enforce strict never-cache guardrails in code:
   - Abstentions (`answered_from_context: false`) are NEVER cached.
   - Low-confidence or ungrounded responses are NEVER cached.
   - Conversational follow-ups (e.g. "what about repeats?") are NEVER cached to prevent context bleed.

## Options Considered
1. **Secondary ChromaDB Collection**: Rejected. Chroma lacks TTL expiration, atomic database flush primitives, and separate eviction policies. Combining query turn caches with knowledge base collections degrades vector query performance on high load.
2. **Postgres with pgvector**: Rejected. Added architectural complexity and excessive resource footprint for an ephemeral cache tier on a 6GB minikube node.
3. **Redis Key-Value with In-Memory Cosine Similarity / Redis VSS**: Accepted. Minimal resource footprint (128Mi requests), sub-millisecond retrieval latency, native TTL support, and instantaneous invalidation upon indexer job completion.

## Consequences
- **Positive**: Average response latency for cached questions drops from $\sim 2.5\text{s}$ to $<15\text{ms}$. Invalidation on re-index is a single atomic command.
- **Negative**: Adds an additional deployment and PVC to the Kubernetes manifest baseline.
- **Monitoring**: We expose `rahbar_cache_hits_total` and `rahbar_cache_misses_total` in Prometheus to observe cache hit ratios.
