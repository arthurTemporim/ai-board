"""Helpers shared by the MCP servers."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from core.models import Draft, DraftKind

CardId = Annotated[int, Field(ge=1, description="Card id")]
Confidence = Annotated[float, Field(ge=0.0, le=1.0, description="0 = guess, 1 = certain")]
RunId = Annotated[
    int | None,
    Field(ge=1, description="Id of the AIRun that produced this, for provenance. Optional."),
]
Rationale = Annotated[str, Field(min_length=1, max_length=500, description="One or two sentences on why")]


def mock_ai() -> bool:
    return os.environ.get("MOCK_AI", "").strip().lower() in {"1", "true", "yes", "on"}


def new_server(name: str, instructions: str) -> FastMCP:
    # Logs go to stderr at WARNING: stdout belongs to the stdio protocol.
    return FastMCP(name, instructions=instructions, log_level="WARNING")


def reserve_stdout_for_protocol() -> None:
    """Point file descriptor 1 at stderr and keep the real stdout only for Python's sys.stdout.

    The stdio transport speaks JSON-RPC on stdout, so a single stray line corrupts it. Native
    libraries (model downloaders, onnxruntime) write to fd 1 directly, bypassing sys.stdout, so
    redirecting at the descriptor level is the only reliable guard.
    """
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = os.fdopen(protocol_fd, "w", encoding="utf-8", newline="\n", buffering=1)


def serve(mcp: FastMCP, argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--selftest" in args:
        tools = asyncio.run(mcp.list_tools())
        print(f"{mcp.name}: " + ", ".join(sorted(t.name for t in tools)))
        return 0
    reserve_stdout_for_protocol()
    mcp.run(transport="stdio")
    return 0


class DraftOut(BaseModel):
    id: int
    card_id: int
    kind: DraftKind
    status: str
    confidence: float | None
    run_id: int | None
    payload: dict
    created_at: datetime


def draft_out(draft: Draft) -> DraftOut:
    return DraftOut(
        id=draft.id,
        card_id=draft.card_id,
        kind=draft.kind,
        status=str(draft.status),
        confidence=draft.confidence,
        run_id=draft.run_id,
        payload=draft.payload,
        created_at=draft.created_at,
    )


class SuggestionOut(BaseModel):
    """Result of a tool that records a proposal. The card itself is never changed by it."""

    draft_id: int
    card_id: int
    kind: DraftKind
    status: Literal["pending"] = "pending"
    created: bool = Field(description="False when an identical pending suggestion already existed")
    applied: Literal[False] = Field(False, description="Always false: a person applies suggestions in the app")
