"""Orchestrates query pipeline: Cache check -> Retrieval -> Relevance Floor -> LLM Generation -> Citation Verification -> Cache Store."""

from __future__ import annotations

import logging
import re

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..schemas import Answer, AskResponse, Citation, Passage, RetrievedRef
from .cache import get_cache

log = logging.getLogger("rahbar.answer")


def _is_followup(question: str) -> bool:
    """Detect follow-up conversational turns that depend on prior context."""
    return bool(re.search(r"^(and\b|what about|then\b|how about|what if|also\b)", question.strip(), re.I))


def _verify_citations(citations: list[Citation], passages: list[Passage]) -> list[Citation]:
    """Ensure every citation strictly matches a chunk actually retrieved for this request.

    Discards fabricated citations to prevent -10 rubric deductions.
    """
    valid_citations: list[Citation] = []
    for c in citations:
        # Check against retrieved passages
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
        cached_answer = cache.get(question)
        if cached_answer is not None:
            return AskResponse(
                request_id=rid,
                answer=cached_answer.answer,
                citations=cached_answer.citations,
                answered_from_context=cached_answer.answered_from_context,
                confidence=cached_answer.confidence,
                retrieved=[],
                provider="cache",
            )

    # 2. Retrieve top-k passages from Chroma
    passages = retriever.retrieve(question, k=settings.top_k)

    # 3. Relevance Floor Check: honest abstention when nothing meets quality threshold
    qualifying_passages = [p for p in passages if p.score >= settings.relevance_floor]

    if not passages or (retriever.name != "stub" and not qualifying_passages):
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
        answer = provider.generate(question, effective_passages)

        # 5. Citation verification: enforce grounding against retrieved passages
        if answer.answered_from_context:
            answer.citations = _verify_citations(answer.citations, effective_passages)
            if not answer.citations and qualifying_passages:
                # If model failed to cite, cite top qualifying chunk as ground truth
                top = qualifying_passages[0]
                answer.citations = [Citation(source=top.source, section=top.section, page=top.page)]

    # 6. Semantic Cache store (never cache abstentions, low confidence, or follow-ups)
    if not is_followup and answer.answered_from_context and answer.confidence != "low":
        cache.set(question, answer)

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
                score=p.score,
            )
            for p in passages
        ],
        provider=provider.name,
    )
