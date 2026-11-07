# ADR 0003: Pluggable LLM Provider Architecture and Constrained Structured Generation

## Status
Accepted · November 2026

## Context
A production RAG service cannot rely on non-deterministic free-form prose. The user interface, citation validator, and CI/CD evaluation harness expect a strict structured contract (`AskResponse` containing typed `Answer` and `Citation` objects).
Furthermore, development environments range from local laptops without GPUs (requiring lightweight stubs or remote hosted endpoints like Groq/Google AI Studio) to cluster deployments with local Ollama daemons. Swapping the inference engine must not require rewriting application logic, and credentials must never leak into Git history or logs.

## Decision
1. **Protocol Interface (`LLMProvider`)**: We implement a uniform protocol with a single method: `generate(question: str, passages: list[Passage]) -> Answer`. We support three concrete providers: `StubProvider` (for deterministic offline unit tests and CI), `OllamaProvider` (for local GPU/CPU execution), and `HostedProvider` (for OpenAI-compatible hosted free-tier APIs).
2. **Constrained Decoding First, Pydantic Validation Second**:
   - We request structured JSON via model options (`format="json"` on Ollama; `response_format={"type": "json_object"}` on hosted APIs).
   - We strictly parse and validate raw responses against Pydantic's `Answer` model.
3. **One Retry with Targeted Error Feedback**: If initial model output violates schema or fails parsing, the system prompts the model a second time with explicit formatting error guidance.
4. **Safe Schema-Valid Fallback**: If the retry fails, the service returns a canned, schema-valid response (`answered_from_context: false`). A user or client NEVER encounters an HTTP 500 internal server error due to malformed model output.
5. **Zero-Secret Governance**: API keys are injected exclusively via Kubernetes Secrets (`LLM_API_KEY`) created dynamically at runtime. The secret field in `Settings` is marked `repr=False` so credentials never appear in structured logs or stack traces.

## Options Considered
1. **LangChain / LlamaIndex Frameworks**: Rejected. Massive dependency footprints, frequent breaking API changes, and unnecessary abstraction layers for a focused pair of endpoints.
2. **Unconstrained Text Generation with Regex Scraping**: Rejected. Prone to hallucinated formatting, syntax errors, and unpredictable citations that break client parsing.
3. **Strict Provider Protocol with Dual-Layer Validation (Engine JSON Mode + Pydantic)**: Accepted. Guarantees 100% adherence to schema contracts while allowing one-file swapping of LLM backends.

## Consequences
- **Positive**: Complete offline testability without network dependencies. Total immunity to HTTP 500 errors caused by LLM syntax deviations.
- **Negative**: Constrained decoding slightly limits chain-of-thought token generation compared to unrestricted free-form generation.
- **Monitoring**: We track `rahbar_fallbacks_total` to measure model compliance rates.
