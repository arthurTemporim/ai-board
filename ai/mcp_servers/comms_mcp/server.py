"""comms_mcp: stakeholder message drafts and templates. Drafts are stored for a person to review;
nothing is ever sent from here."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ai.mcp_servers.common import CardId, Confidence, DraftOut, RunId, SuggestionOut, draft_out, new_server
from ai.mcp_servers.comms_mcp.templates import Audience, TemplateOut, load_templates
from core.db import session_scope
from core.models import DraftKind, DraftStatus
from core.services.suggestions import SuggestionService

mcp = new_server(
    "comms_mcp",
    "Save and list stakeholder message drafts, and fetch message templates. Nothing is sent.",
)


class DraftList(BaseModel):
    drafts: list[DraftOut]


class TemplateList(BaseModel):
    templates: list[TemplateOut]


@mcp.tool()
def save_draft(
    card_id: CardId,
    audience: Annotated[Audience, Field(description="Who the message is for")],
    subject: Annotated[str, Field(min_length=1, max_length=120, description="Message subject line")],
    body: Annotated[str, Field(min_length=1, max_length=2000, description="Message body, plain text")],
    tone: Annotated[
        Literal["neutral", "apologetic", "celebratory"], Field(description="Tone of the message")
    ] = "neutral",
    confidence: Confidence | None = None,
    run_id: RunId = None,
) -> SuggestionOut:
    """Store a stakeholder message draft for a card as a pending draft for a person to review and send. Nothing is sent. An identical pending draft is not duplicated."""
    with session_scope() as session:
        draft, created = SuggestionService(session).save_draft(
            card_id,
            DraftKind.comms,
            {"audience": audience, "subject": subject.strip(), "body": body.strip(), "tone": tone},
            confidence=confidence,
            run_id=run_id,
        )
        return SuggestionOut(draft_id=draft.id, card_id=card_id, kind=draft.kind, created=created)


@mcp.tool()
def list_drafts(
    card_id: CardId,
    kind: Annotated[
        DraftKind | None, Field(description="Kind of draft; defaults to 'comms'. Pass null for every kind")
    ] = DraftKind.comms,
    status: Annotated[DraftStatus | None, Field(description="Only drafts with this status")] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="Maximum number of drafts to return")] = 50,
) -> DraftList:
    """List a card's drafts, newest first. Defaults to message drafts (kind 'comms'). Read-only."""
    with session_scope() as session:
        drafts = SuggestionService(session).list_drafts(card_id, kind, status, limit)
        return DraftList(drafts=[draft_out(d) for d in drafts])


@mcp.tool()
def get_templates(
    audience: Annotated[Audience | None, Field(description="Only templates for this audience")] = None,
) -> TemplateList:
    """Get message templates (name, audience, subject, body with {placeholders}) from the YAML config. Read-only; nothing is rendered or sent."""
    return TemplateList(templates=load_templates(audience))
