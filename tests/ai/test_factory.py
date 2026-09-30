from __future__ import annotations

import pytest

from ai.llm.errors import LLMCallError
from ai.llm.factory import AnthropicLLM, get_llm, is_transient, mock_enabled
from ai.llm.mock import MockLLM
from ai.llm.registry import default_registry
from ai.llm.schemas import SuggestTagsOutput
from tests.ai.samples import SAMPLE_VARIABLES


class Boom(Exception):
    def __init__(self, status_code=None):
        super().__init__(f"boom {status_code}")
        self.status_code = status_code


class FakeChat:
    """Stands in for ChatAnthropic: replays a script of replies/exceptions."""

    def __init__(self, script, record):
        self.script, self.record = script, record

    def with_structured_output(self, schema, include_raw=False):
        assert include_raw is True
        self.record["schema"] = schema
        return self

    def invoke(self, messages):
        self.record.setdefault("calls", []).append(messages)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class Usage:
    usage_metadata = {"input_tokens": 100, "output_tokens": 20}


def good(confidence=0.9):
    parsed = SuggestTagsOutput(tags=[], confidence=confidence, rationale="ok")
    return {"raw": Usage(), "parsed": parsed, "parsing_error": None}


def bad(problem="confidence: field required"):
    return {"raw": Usage(), "parsed": None, "parsing_error": ValueError(problem)}


def build(script, **kwargs):
    record: dict = {"kwargs": []}
    sleeps: list[float] = []

    def factory(**chat_kwargs):
        record["kwargs"].append(chat_kwargs)
        return FakeChat(script, record)

    llm = AnthropicLLM(chat_factory=factory, sleep=sleeps.append, jitter=lambda: 1.0, **kwargs)
    return llm, record, sleeps


@pytest.fixture
def prompt():
    return default_registry().render("suggest_tags", SAMPLE_VARIABLES["suggest_tags"])


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("MOCK_AI", "AI_MODEL_OVERRIDE", "LITELLM_BASE_URL", "LITELLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# ---- model and request configuration ----------------------------------------------------------
def test_model_temperature_and_limits_come_from_the_prompt_frontmatter(prompt):
    llm, record, _ = build([good()], timeout=12.0, api_key="k")
    result = llm.invoke(prompt)

    kwargs = record["kwargs"][0]
    assert kwargs["model"] == "claude-haiku-4-5-20251001" == prompt.model
    assert kwargs["temperature"] == prompt.temperature
    assert kwargs["max_tokens"] == prompt.max_tokens
    assert kwargs["timeout"] == 12.0
    assert kwargs["max_retries"] == 0  # retries are ours, not the SDK's
    assert "base_url" not in kwargs
    assert record["schema"] is SuggestTagsOutput
    assert isinstance(result.output, SuggestTagsOutput)
    assert (result.tokens_in, result.tokens_out, result.attempts) == (100, 20, 1)


def test_messages_are_sent_as_system_then_user(prompt):
    llm, record, _ = build([good()], api_key="k")
    llm.invoke(prompt)
    assert [role for role, _ in record["calls"][0]] == ["system", "user"]


def test_different_prompts_use_different_models():
    llm, record, _ = build([good(), good()], api_key="k")
    llm.invoke(default_registry().render("suggest_tags", SAMPLE_VARIABLES["suggest_tags"]))
    tags_model = record["kwargs"][-1]["model"]
    scored = default_registry().render("score_request", SAMPLE_VARIABLES["score_request"])
    assert llm.model_for(scored) == "claude-sonnet-5-5" != tags_model


def test_model_override_env_wins(monkeypatch, prompt):
    monkeypatch.setenv("AI_MODEL_OVERRIDE", "my-litellm-alias")
    llm, record, _ = build([good()], api_key="k")
    llm.invoke(prompt)
    assert llm.model_for(prompt) == record["kwargs"][0]["model"] == "my-litellm-alias"


def test_litellm_proxy_settings_are_used_when_configured(monkeypatch, prompt):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://proxy:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "proxy-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "direct-key")
    llm, record, _ = build([good()])
    llm.invoke(prompt)
    assert (record["kwargs"][0]["base_url"], record["kwargs"][0]["api_key"]) == ("http://proxy:4000", "proxy-key")


def test_direct_anthropic_key_is_used_without_a_proxy(monkeypatch, prompt):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "direct-key")
    llm, record, _ = build([good()])
    llm.invoke(prompt)
    assert record["kwargs"][0]["api_key"] == "direct-key" and "base_url" not in record["kwargs"][0]


def test_missing_key_fails_fast_with_a_helpful_message():
    with pytest.raises(LLMCallError, match="No API key.*MOCK_AI=1"):
        AnthropicLLM()


# ---- retry with backoff -----------------------------------------------------------------------
def test_transient_errors_are_retried_with_exponential_backoff(prompt):
    llm, record, sleeps = build([Boom(429), Boom(503), good()], api_key="k", max_attempts=3, base_delay=1.0)
    result = llm.invoke(prompt)
    assert result.attempts == 3
    assert sleeps == [1.0, 2.0]  # jitter pinned to 1.0, so full delay


def test_backoff_is_capped_and_jittered(prompt):
    llm, _, sleeps = build([Boom(500)] * 5 + [good()], api_key="k", max_attempts=6, base_delay=4.0, max_delay=10.0)
    llm._jitter = lambda: 0.0
    llm.invoke(prompt)
    assert sleeps == [2.0, 4.0, 5.0, 5.0, 5.0]  # half of min(cap, base * 2**n)


def test_retries_are_exhausted_then_reported(prompt):
    llm, record, sleeps = build([Boom(503)] * 3, api_key="k", max_attempts=3)
    with pytest.raises(LLMCallError, match=r"suggest_tags@1.*failed after 3 attempt.*503"):
        llm.invoke(prompt)
    assert len(sleeps) == 2 and len(record["calls"]) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(prompt, status):
    llm, record, sleeps = build([Boom(status), good()], api_key="k")
    with pytest.raises(LLMCallError):
        llm.invoke(prompt)
    assert sleeps == [] and len(record["calls"]) == 1


def test_timeouts_and_connection_errors_are_transient(prompt):
    llm, _, sleeps = build([TimeoutError("slow"), ConnectionError("reset"), good()], api_key="k")
    assert llm.invoke(prompt).attempts == 3 and len(sleeps) == 2


@pytest.mark.parametrize(
    "exc, expected",
    [
        (Boom(429), True),
        (Boom(408), True),
        (Boom(529), True),
        (Boom(400), False),
        (Boom(None), False),
        (ValueError("x"), False),
        (TimeoutError(), True),
    ],
)
def test_is_transient(exc, expected):
    assert is_transient(exc) is expected


def test_anthropic_connection_errors_are_transient():
    import anthropic
    import httpx

    request = httpx.Request("POST", "http://x")
    assert is_transient(anthropic.APIConnectionError(request=request))
    assert is_transient(anthropic.APITimeoutError(request=request))


# ---- structured output failures ---------------------------------------------------------------
def test_schema_failure_gets_one_repair_attempt_with_the_error_fed_back(prompt):
    llm, record, sleeps = build([bad("tags: too many"), good()], api_key="k")
    result = llm.invoke(prompt)
    assert result.attempts == 2 and sleeps == []
    repair = record["calls"][1][-1]
    assert repair[0] == "user" and "tags: too many" in repair[1] and "schema" in repair[1]
    assert len(record["calls"][1]) == len(record["calls"][0]) + 1
    assert (result.tokens_in, result.tokens_out) == (200, 40)  # usage is summed across calls


def test_two_schema_failures_raise(prompt):
    llm, record, _ = build([bad("first"), bad("second")], api_key="k")
    with pytest.raises(LLMCallError, match="did not match the schema twice.*second"):
        llm.invoke(prompt)
    assert len(record["calls"]) == 2


def test_parsed_value_of_the_wrong_type_counts_as_a_failure(prompt):
    llm, _, _ = build([{"raw": Usage(), "parsed": {"tags": []}, "parsing_error": None}, good()], api_key="k")
    assert llm.invoke(prompt).attempts == 2


# ---- choosing the implementation --------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected", [("1", True), ("true", True), ("YES", True), ("0", False), ("", False), ("no", False)]
)
def test_mock_enabled_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("MOCK_AI", value)
    assert mock_enabled() is expected


def test_get_llm_returns_the_mock_when_mock_ai_is_set(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")
    assert isinstance(get_llm(), MockLLM)


def test_get_llm_returns_claude_otherwise(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(get_llm(), AnthropicLLM)


def test_real_chat_anthropic_is_constructed_with_our_settings(monkeypatch, prompt):
    """No network: only checks the real ChatAnthropic accepts the arguments we pass."""
    monkeypatch.setenv("LITELLM_BASE_URL", "http://proxy:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "k")
    chat = AnthropicLLM(timeout=30)._chat(prompt)
    assert chat.model == "claude-haiku-4-5-20251001"
    assert chat.max_retries == 0 and chat.default_request_timeout == 30
    assert chat.anthropic_api_url == "http://proxy:4000"
