from __future__ import annotations

import pytest

from core.redact import redact


def test_none_and_empty_pass_through():
    assert redact(None) is None and redact("") == ""


def test_configured_secrets_are_removed_wherever_they_appear(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "abc123secretvalue")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "zzzTOPSECRETkey")
    text = "401 from http://proxy/?key=abc123secretvalue while using zzzTOPSECRETkey and abc123secretvalue again"
    out = redact(text)
    assert "abc123secretvalue" not in out and "zzzTOPSECRETkey" not in out
    assert out.count("[redacted]") == 3


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "bearer abcdef0123456789",
        "used key sk-ant-api03-AbCdEf_123456789",
        'headers={"x-api-key": "k_live_12345678"}',
        "api_key=hunter2hunter2",
        "OPENAI_API_KEY: sk-proj-abcdefghijkl",
    ],
)
def test_common_credential_shapes_are_removed(text):
    out = redact(text)
    assert "[redacted]" in out
    for leaked in ("eyJhbGci", "abcdef0123456789", "AbCdEf_123", "k_live_12345678", "hunter2hunter2", "abcdefghijkl"):
        assert leaked not in out


def test_ordinary_text_and_short_values_are_left_alone(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "key")  # too short to be treated as a secret: it would blank words
    text = "The key to a good key result is a clear key metric; skill-level 5 is fine."
    assert redact(text) == text
