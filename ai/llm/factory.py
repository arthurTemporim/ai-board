"""LLM factory: Claude through langchain-anthropic with structured output, or the mock.

Configuration (environment)
    MOCK_AI=1              use MockLLM: no network, no API key, deterministic output
    ANTHROPIC_API_KEY      key for talking to Anthropic directly
    LITELLM_BASE_URL       if set, send requests to this litellm proxy (its Anthropic-format
    LITELLM_API_KEY        endpoint) using this key instead; model names are then litellm aliases
    AI_MODEL_OVERRIDE      force every prompt onto this model name (handy for litellm aliases)
    LLM_TIMEOUT_SECONDS    per-request timeout, default 60
    LLM_MAX_ATTEMPTS       tries per call for transient failures, default 3

The model for each prompt comes from its frontmatter; nothing here hard-codes one.
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from ai.llm.errors import LLMCallError
from ai.llm.registry import RenderedPrompt

_TRUTHY = {"1", "true", "yes", "on"}


def mock_enabled() -> bool:
    return os.environ.get("MOCK_AI", "").strip().lower() in _TRUTHY


@dataclass
class LLMResult:
    output: BaseModel
    tokens_in: int | None = None
    tokens_out: int | None = None
    attempts: int = 1


class StructuredLLM(Protocol):
    def model_for(self, prompt: RenderedPrompt) -> str:
        """The model name that will actually be used (after overrides)."""

    def invoke(self, prompt: RenderedPrompt) -> LLMResult:
        """Run the prompt and return output validated against prompt.output_schema."""


def is_transient(exc: BaseException) -> bool:
    """Worth retrying: timeouts, dropped connections, rate limits and server-side errors."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 429) or status >= 500
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    try:
        import anthropic

        return isinstance(exc, anthropic.APIConnectionError)  # includes APITimeoutError
    except ImportError:  # pragma: no cover
        return False


ChatFactory = Callable[..., Any]


def _default_chat_factory(**kwargs: Any) -> Any:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(**kwargs)


class AnthropicLLM:
    def __init__(
        self,
        *,
        chat_factory: ChatFactory | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
        base_delay: float = 1.0,
        max_delay: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        self.base_url = base_url if base_url is not None else (os.environ.get("LITELLM_BASE_URL") or None)
        if api_key is not None:
            self.api_key = api_key
        elif self.base_url:
            self.api_key = os.environ.get("LITELLM_API_KEY") or None
        else:
            self.api_key = os.environ.get("ANTHROPIC_API_KEY") or None
        self.timeout = timeout if timeout is not None else float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
        self.max_attempts = max_attempts if max_attempts is not None else int(os.environ.get("LLM_MAX_ATTEMPTS", "3"))
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._jitter = jitter
        self._chat_factory = chat_factory
        if chat_factory is None and not self.api_key:
            raise LLMCallError(
                "No API key configured. Set ANTHROPIC_API_KEY (or LITELLM_BASE_URL and LITELLM_API_KEY), "
                "or set MOCK_AI=1 to run without a model."
            )

    def model_for(self, prompt: RenderedPrompt) -> str:
        return os.environ.get("AI_MODEL_OVERRIDE") or prompt.model

    def _chat(self, prompt: RenderedPrompt) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model_for(prompt),
            "temperature": prompt.temperature,
            "max_tokens": prompt.max_tokens,
            "timeout": self.timeout,
            "max_retries": 0,  # retries are done here so they are counted and logged once
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return (self._chat_factory or _default_chat_factory)(**kwargs)

    def _delay(self, attempt: int) -> float:
        ceiling = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        return ceiling * (0.5 + 0.5 * self._jitter())

    def invoke(self, prompt: RenderedPrompt) -> LLMResult:
        structured = self._chat(prompt).with_structured_output(prompt.output_schema, include_raw=True)
        messages: list[tuple[str, str]] = [(m.role, m.content) for m in prompt.messages]
        tokens_in = tokens_out = 0
        transient_failures = 0
        repaired = False
        calls = 0

        while True:
            calls += 1
            try:
                reply = structured.invoke(messages)
            except Exception as exc:
                transient_failures += 1
                if is_transient(exc) and transient_failures < self.max_attempts:
                    self._sleep(self._delay(transient_failures))
                    continue
                raise LLMCallError(
                    f"{prompt.ref}: model call failed after {calls} attempt(s): {type(exc).__name__}: {exc}"
                ) from exc

            usage = getattr(reply.get("raw"), "usage_metadata", None) or {}
            tokens_in += int(usage.get("input_tokens", 0))
            tokens_out += int(usage.get("output_tokens", 0))

            parsed, problem = reply.get("parsed"), reply.get("parsing_error")
            if isinstance(parsed, prompt.output_schema) and problem is None:
                return LLMResult(parsed, tokens_in or None, tokens_out or None, calls)

            if repaired:
                raise LLMCallError(f"{prompt.ref}: model output did not match the schema twice: {problem}")
            repaired = True
            messages = [
                *messages,
                (
                    "user",
                    f"Your previous answer was rejected because it did not match the required schema: {problem}. "
                    "Answer again with a result that satisfies the schema.",
                ),
            ]


def get_llm() -> StructuredLLM:
    """MockLLM when MOCK_AI is set, otherwise Claude."""
    if mock_enabled():
        from ai.llm.mock import MockLLM

        return MockLLM()
    return AnthropicLLM()
