"""Domain errors. The API layer maps them to HTTP responses; core knows nothing about HTTP."""

from __future__ import annotations


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str, *, code: str | None = None, details: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or []


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class InvalidInputError(DomainError):
    code = "invalid_input"


class ForbiddenError(DomainError):
    code = "forbidden"


class UnauthorizedError(DomainError):
    code = "actor_required"
