"""Scrub secrets from text before it is stored or shown (error messages, run logs).

An exception raised by an HTTP client can quote a URL, a header or a response body; none of that
should end up in the AIRun table or an API response with a key in it.
"""

from __future__ import annotations

import os
import re

SECRET_ENV_VARS = ("LITELLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
MIN_SECRET_LENGTH = 6  # shorter values would blank out ordinary words
_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{8,}=*"),
    re.compile(r"(?i)(api[_-]?key|authorization|x-api-key)(\"?\s*[:=]\s*\"?)[^\s\"',}]{6,}"),
)


def redact(text: str | None) -> str | None:
    if not text:
        return text
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name, "")
        if len(value) >= MIN_SECRET_LENGTH:
            text = text.replace(value, "[redacted]")
    text = _PATTERNS[0].sub("[redacted]", text)
    text = _PATTERNS[1].sub("Bearer [redacted]", text)
    return _PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
