"""LLM factory: structured-output chat models, or the mock.

Providers (LLM_PROVIDER)
    openai      any OpenAI-compatible endpoint via langchain-openai. This is what a litellm proxy
                in front of local models exposes. Default whenever LITELLM_BASE_URL is set.
    anthropic   Claude via langchain-anthropic (direct, or a proxy's Anthropic-format endpoint).

Configuration (environment)
    MOCK_AI=1              use MockLLM: no network, no API key, deterministic output
    LITELLM_BASE_URL       proxy address, with or without a trailing /v1
    LITELLM_API_KEY        key for the proxy
    ANTHROPIC_API_KEY      key for talking to Anthropic directly (provider "anthropic")
    LLM_STRUCTURED_METHOD  how the openai provider asks for structured output: json_schema
                           (default), function_calling or json_mode. Local models served through
                           Ollama accept only json_schema and json_mode, not tool calls.
    AI_MODEL_OVERRIDE      force every prompt onto this model name
    LLM_TIMEOUT_SECONDS    per-request timeout, default 60 (local models can be slow: raise it)
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
    for module_name in ("anthropic", "openai"):
        try:
            module = __import__(module_name)
        except ImportError:  # pragma: no cover
            continue
        if isinstance(exc, module.APIConnectionError):  # includes APITimeoutError
            return True
    return False


ChatFactory = Callable[..., Any]

STRUCTURED_METHODS = ("json_schema", "function_calling", "json_mode")


class ChatModelLLM:
    """Retry, backoff and schema-repair logic shared by the providers.

    Subclasses decide where to connect (_resolve_endpoint), how to build the chat model
    (_default_factory, _chat_kwargs) and how to ask it for structured output (_structured).
    """

    provider = "base"

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
        self.base_url, self.api_key = self._resolve_endpoint(base_url, api_key, offline=chat_factory is not None)
        self.timeout = timeout if timeout is not None else float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
        self.max_attempts = max_attempts if max_attempts is not None else int(os.environ.get("LLM_MAX_ATTEMPTS", "3"))
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._jitter = jitter
        self._chat_factory = chat_factory

    # -- provider hooks --
    def _resolve_endpoint(
        self, base_url: str | None, api_key: str | None, offline: bool
    ) -> tuple[str | None, str | None]:
        raise NotImplementedError

    def _default_factory(self) -> ChatFactory:
        raise NotImplementedError

    def _structured(self, chat: Any, schema: type[BaseModel]) -> Any:
        return chat.with_structured_output(schema, include_raw=True)

    # -- shared behaviour --
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
        return (self._chat_factory or self._default_factory())(**kwargs)

    def _delay(self, attempt: int) -> float:
        ceiling = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        return ceiling * (0.5 + 0.5 * self._jitter())

    def invoke(self, prompt: RenderedPrompt) -> LLMResult:
        structured = self._structured(self._chat(prompt), prompt.output_schema)
        messages: list[tuple[str, str]] = [(m.role, m.content) for m in prompt.messages]
        tokens_in = tokens_out = 0
        transient_failures = 0
        repaired = False
        calls = 0

        while True:
            calls += 1
            reply: dict[str, Any] = {}
            try:
                reply = structured.invoke(messages)
                problem: Any = reply.get("parsing_error")
            except Exception as exc:
                failure = LLMCallError(
                    f"{prompt.ref}: model call failed after {calls} attempt(s): {type(exc).__name__}: {exc}"
                )
                if is_transient(exc):
                    transient_failures += 1
                    if transient_failures < self.max_attempts:
                        self._sleep(self._delay(transient_failures))
                        continue
                    raise failure from exc
                if not isinstance(exc, ValueError):
                    raise failure from exc
                # Invalid JSON, schema validation or an output-parser error. Some providers raise
                # these instead of returning them; they are the model's fault, not the network's.
                problem = exc

            usage = getattr(reply.get("raw"), "usage_metadata", None) or {}
            tokens_in += int(usage.get("input_tokens", 0))
            tokens_out += int(usage.get("output_tokens", 0))

            parsed = reply.get("parsed")
            if isinstance(parsed, prompt.output_schema) and problem is None:
                return LLMResult(parsed, tokens_in or None, tokens_out or None, calls)

            if repaired:
                raise LLMCallError(
                    f"{prompt.ref}: model output did not match the schema twice: {problem}. "
                    "If this repeats, the model may be running out of output tokens (raise max_tokens in the "
                    "prompt's frontmatter) or not following the schema."
                )
            repaired = True
            messages = [
                *messages,
                (
                    "user",
                    f"Your previous answer was rejected because it did not match the required schema: {problem}. "
                    "Answer again with a result that satisfies the schema.",
                ),
            ]


class AnthropicLLM(ChatModelLLM):
    """Claude through langchain-anthropic; structured output uses tool calling."""

    provider = "anthropic"

    def _resolve_endpoint(self, base_url, api_key, offline):
        url = base_url if base_url is not None else (os.environ.get("LITELLM_BASE_URL") or None)
        if api_key is not None:
            key = api_key
        elif url:
            key = os.environ.get("LITELLM_API_KEY") or None
        else:
            key = os.environ.get("ANTHROPIC_API_KEY") or None
        if not offline and not key:
            raise LLMCallError(
                "No API key configured. Set ANTHROPIC_API_KEY (or LITELLM_BASE_URL and LITELLM_API_KEY), "
                "or set MOCK_AI=1 to run without a model."
            )
        return url, key

    def _default_factory(self) -> ChatFactory:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic


class OpenAICompatLLM(ChatModelLLM):
    """Any OpenAI-compatible endpoint (a litellm proxy in front of local models) via langchain-openai."""

    provider = "openai"

    def __init__(self, *, structured_method: str | None = None, **kwargs: Any):
        self.structured_method = structured_method or os.environ.get("LLM_STRUCTURED_METHOD") or "json_schema"
        if self.structured_method not in STRUCTURED_METHODS:
            raise LLMCallError(
                f"LLM_STRUCTURED_METHOD must be one of {', '.join(STRUCTURED_METHODS)}, got '{self.structured_method}'"
            )
        super().__init__(**kwargs)

    def _resolve_endpoint(self, base_url, api_key, offline):
        url = (base_url if base_url is not None else os.environ.get("LITELLM_BASE_URL")) or None
        if url is None and not offline:
            raise LLMCallError(
                "LITELLM_BASE_URL is not set. Point it at your litellm proxy (for example http://host:4000), "
                "or set MOCK_AI=1 to run without a model."
            )
        if url is not None:
            url = url.rstrip("/")
            if not url.endswith("/v1"):
                url += "/v1"
        key = api_key if api_key is not None else (os.environ.get("LITELLM_API_KEY") or None)
        return url, key or "not-needed"  # some proxies need no key, but the client wants a value

    def _default_factory(self) -> ChatFactory:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI

    def _structured(self, chat: Any, schema: type[BaseModel]) -> Any:
        return chat.with_structured_output(schema, include_raw=True, method=self.structured_method)


def llm_provider() -> str:
    """LLM_PROVIDER if set; otherwise openai when a litellm proxy is configured, else anthropic."""
    chosen = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if chosen:
        if chosen not in ("openai", "anthropic"):
            raise LLMCallError(f"LLM_PROVIDER must be 'openai' or 'anthropic', got '{chosen}'")
        return chosen
    return "openai" if os.environ.get("LITELLM_BASE_URL") else "anthropic"


def get_llm() -> StructuredLLM:
    """MockLLM when MOCK_AI is set, otherwise the configured provider."""
    if mock_enabled():
        from ai.llm.mock import MockLLM

        return MockLLM()
    return OpenAICompatLLM() if llm_provider() == "openai" else AnthropicLLM()
