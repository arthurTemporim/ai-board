from __future__ import annotations


class PromptError(Exception):
    """Base class for problems with prompt files or registry."""


class PromptNotFoundError(PromptError):
    """Unknown prompt name, or a version that has no file."""


class PromptFormatError(PromptError):
    """A prompt file or registry entry is malformed."""


class PromptVariableError(PromptError):
    """render() was called with missing or unexpected variables."""


class LLMCallError(Exception):
    """The model call failed after retries, or returned output that does not match the schema."""
