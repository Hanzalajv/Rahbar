"""Hosted LLM provider with JSON mode, validation retry, and safe fallback."""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...schemas import Answer, Passage
from .ollama import SYSTEM_INSTRUCTION, build_prompt

log = logging.getLogger("rahbar.providers.hosted")


class HostedProvider:
    name = "hosted"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _call_api(self, prompt: str) -> str:
        base_url = self.settings.hosted_base_url.rstrip("/") or "https://api.openai.com/v1"
        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.llm_api_key}",
        }
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": self.settings.temperature,
        }
        with httpx.Client(timeout=httpx.Timeout(45.0, connect=5.0)) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        """Generate answer via hosted endpoint with 1 validation retry and fallback."""
        if not passages:
            return Answer(
                reasoning="No passages available to answer question.",
                answer="The university regulations corpus does not contain information on this topic.",
                citations=[],
                answered_from_context=False,
                confidence="low",
            )

        prompt = build_prompt(question, passages)

        # Attempt 1
        try:
            raw = self._call_api(prompt)
            return Answer.model_validate_json(raw)
        except (ValidationError, Exception) as err1:
            log.warning("hosted provider first attempt failed validation: %s; retrying once", err1)

        # Retry once on validation failure
        try:
            retry_prompt = prompt + "\n\nCRITICAL: Previous response was not valid JSON. Return strictly valid JSON."
            raw_retry = self._call_api(retry_prompt)
            return Answer.model_validate_json(raw_retry)
        except (ValidationError, Exception) as err2:
            log.error("hosted provider retry failed: %s; using safe fallback", err2)

        # Safe fallback response: schema-valid, never 500
        return Answer(
            reasoning="Fallback: Hosted provider response could not be validated against the Answer schema.",
            answer="I am unable to provide a validated answer from the current documents.",
            citations=[],
            answered_from_context=False,
            confidence="low",
        )
