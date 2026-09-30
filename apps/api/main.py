from __future__ import annotations

import os

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from apps.api.deps import SessionDep
from apps.api.errors import install_error_handlers
from apps.api.routers import boards, cards, engagement, tags, users
from apps.api.schemas import ErrorResponse


def create_app() -> FastAPI:
    app = FastAPI(
        title="Feature Request Board API",
        version="0.1.0",
        description="Kanban board for product feature requests.",
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )

    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in origins if o],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    @app.get("/health", tags=["health"])
    def health(session: SessionDep, response: Response) -> dict[str, str]:
        try:
            session.execute(text("SELECT 1"))
        except Exception:
            response.status_code = 503
            return {"status": "unavailable"}
        return {"status": "ok"}

    for router in (users.router, boards.router, cards.router, tags.router, engagement.router):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
