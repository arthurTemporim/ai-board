"""One error shape for every failure: {"error": {"code", "message", "details": [{"field", "message"}]}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
    UnauthorizedError,
)
from core.redact import redacted_traceback

log = logging.getLogger(__name__)

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    InvalidInputError: 422,
    ForbiddenError: 403,
    UnauthorizedError: 401,
    RateLimitError: 429,
}


def error_response(status: int, code: str, message: str, details: list[dict] | None = None):
    body = {"error": {"code": code, "message": message, "details": details or []}}
    return JSONResponse(status_code=status, content=body)


def _field_path(loc: tuple) -> str | None:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) or None


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        status = next((s for t, s in STATUS_BY_ERROR.items() if isinstance(exc, t)), 400)
        return error_response(status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [
            {"field": _field_path(e["loc"]), "message": e["msg"].removeprefix("Value error, ")} for e in exc.errors()
        ]
        return error_response(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        codes = {404: "not_found", 405: "method_not_allowed"}
        return error_response(exc.status_code, codes.get(exc.status_code, "http_error"), str(exc.detail))

    @app.exception_handler(IntegrityError)
    async def integrity_error(_: Request, exc: IntegrityError):
        log.warning("integrity error: %s", exc)
        return error_response(409, "integrity_error", "The change conflicts with existing data")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.error("unhandled error on %s %s\n%s", request.method, request.url.path, redacted_traceback())
        return error_response(500, "internal_error", "Internal server error")
