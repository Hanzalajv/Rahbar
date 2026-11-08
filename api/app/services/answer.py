"""Orchestrates query pipeline: Cache check -> Retrieval -> Relevance Floor -> LLM Generation -> Citation Verification -> Cache Store."""

from __future__ import annotations

import logging
import re
import time

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..routes import metrics
from ..schemas import Answer, AskResponse, Citation, Passage, RetrievedRef
from .cache import get_cache

log = logging.getLogger("rahbar.answer")


def _is_followup(question: str) -> bool:
    """Detect follow-up conversational turns that depend on prior context."""
    return bool(re.search(r"^(and\b|what about|then\b|how about|what if|also\b)", question.strip(), re.I))


def _verify_citations(citations: list[Citation], passages: list[Passage]) -> list[Citation]:
    """Ensure every citation strictly matches a chunk actually retrieved for this request."""
    valid_citations: list[Citation] = []
    for c in citations:
        match_found = any(
            p.source.lower() == c.source.lower()
            and (c.section.lower() in p.section.lower() or p.section.lower() in c.section.lower())
            for p in passages
        )
        if match_found:
            valid_citations.append(c)
        else:
            log.warning("discarded ungrounded citation: %s (%s)", c.source, c.section)
    return valid_citations


def answer_question(
    question: str,
    retriever: Retriever,
    provider: LLMProvider,
    settings: Settings,
    skip_cache: bool = False,
) -> AskResponse:
    """Executes the full 8-step production query pipeline."""
    rid = request_id_var.get()
    cache = get_cache(settings)

    # 1. Semantic cache check (skip for follow-up turns or if requested)
    is_followup = _is_followup(question)
    if not skip_cache and not is_followup:
        cached_ans = cache.get(question)
        if cached_ans is not None:
            metrics.CACHE_HITS.inc()
            return AskResponse(
                request_id=rid,
                answer=cached_ans.answer,
                citations=cached_ans.citations,
                answered_from_context=cached_ans.answered_from_context,
                confidence=cached_ans.confidence,
                retrieved=cached_ans.retrieved,
                provider="cache",
            )
        metrics.CACHE_MISSES.inc()

    # 2. Retrieve top-k passages from Chroma
    t_ret_start = time.perf_counter()
    try:
        passages = retriever.retrieve(question, k=settings.top_k)
    except Exception as exc:
        log.error("retrieval failed: %s", exc)
        passages = []
    metrics.RETRIEVAL_LATENCY.observe(time.perf_counter() - t_ret_start)

    # 3. Relevance Floor Check: honest abstention when nothing meets quality threshold
    qualifying_passages = [p for p in passages if p.score >= settings.relevance_floor]

    if not passages or (retriever.name != "stub" and not qualifying_passages):
        metrics.ABSTENTIONS.inc()
        log.info(
            "abstaining: no passages cleared relevance floor",
            extra={"top_score": passages[0].score if passages else 0.0, "floor": settings.relevance_floor},
        )
        answer = Answer(
            reasoning="All retrieved passages fell below the relevance floor threshold.",
            answer="The university regulations corpus does not contain sufficient information to answer this question.",
            citations=[],
            answered_from_context=False,
            confidence="low",
        )
    else:
        # 4. Generate answer using LLM with grounded prompt
        effective_passages = qualifying_passages if qualifying_passages else passages
        prompt_token_est = int(len(question.split()) * 1.3) + sum(int(len(p.text.split()) * 1.3) for p in effective_passages)
        metrics.TOKENS_IN.inc(prompt_token_est)

        t_gen_start = time.perf_counter()
        answer = provider.generate(question, effective_passages)
        metrics.GENERATION_LATENCY.observe(time.perf_counter() - t_gen_start)

        out_token_est = int(len(answer.answer.split()) * 1.3)
        metrics.TOKENS_OUT.inc(out_token_est)

        if "Fallback" in answer.reasoning:
            metrics.FALLBACKS.inc()

        # 5. Citation verification: enforce grounding against retrieved passages
        if answer.answered_from_context:
            answer.citations = _verify_citations(answer.citations, effective_passages)
            if not answer.citations and qualifying_passages:
                top = qualifying_passages[0]
                answer.citations = [Citation(source=top.source, section=top.section, page=top.page)]
        else:
            metrics.ABSTENTIONS.inc()

    # 6. Semantic Cache store (never cache abstentions, low confidence, or follow-ups)
    if not is_followup and answer.answered_from_context and answer.confidence != "low":
        cache.set(question, answer, retrieved=passages)

    # 7. Structured observability logging
    log.info(
        "answered",
        extra={
            "retriever": retriever.name,
            "provider": provider.name,
            "top_k": settings.top_k,
            "chunk_ids": [p.chunk_id for p in passages],
            "scores": [round(p.score, 4) for p in passages],
            "answered_from_context": answer.answered_from_context,
            "citations_count": len(answer.citations),
        },
    )

    return AskResponse(
        request_id=rid,
        answer=answer.answer,
        citations=answer.citations,
        answered_from_context=answer.answered_from_context,
        confidence=answer.confidence,
        retrieved=[
            RetrievedRef(
                chunk_id=p.chunk_id,
                source=p.source,
                section=p.section,
                page=p.page,
                text=p.text,
                score=p.score,
            )
            for p in passages
        ],
        provider=provider.name,
    )
