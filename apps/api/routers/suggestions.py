"""AI suggestions on a card, and the human decisions that apply or reject them."""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.deps import ActorDep, SessionDep
from apps.api.schemas import AcceptRequest, AuditRead, DecisionRead, RejectRequest, SuggestionRead
from core.services.decisions import DecisionService

router = APIRouter(prefix="/cards/{card_id}", tags=["suggestions"])


@router.get(
    "/suggestions",
    response_model=list[SuggestionRead],
    summary="AI suggestions on a card",
    description="Duplicates and related cards (with confidence and reasoning), problem grouping, tags, scores per "
    "criterion, the decision brief and drafts. Pending ones by default.",
)
def list_suggestions(card_id: int, session: SessionDep, include_decided: bool = False):
    return DecisionService(session).list(card_id, include_decided=include_decided)


@router.post(
    "/suggestions/{suggestion_id}/accept",
    response_model=DecisionRead,
    summary="Accept a suggestion: apply it and write an audit entry",
    description="Needs the X-User-Id header. `edits` changes the suggestion's editable fields first. For a score, "
    "`override` changes ratings and requires a reason. Accepting a duplicate moves its votes to the primary card and "
    "keeps the cards linked.",
)
def accept_suggestion(card_id: int, suggestion_id: str, body: AcceptRequest, session: SessionDep, actor: ActorDep):
    override = body.override.model_dump() if body.override else None
    return DecisionService(session).accept(card_id, suggestion_id, actor.id, edits=body.edits, override=override)


@router.post(
    "/suggestions/{suggestion_id}/reject",
    response_model=DecisionRead,
    summary="Reject a suggestion (it is not proposed again) and write an audit entry",
)
def reject_suggestion(card_id: int, suggestion_id: str, body: RejectRequest, session: SessionDep, actor: ActorDep):
    return DecisionService(session).reject(card_id, suggestion_id, actor.id, reason=body.reason)


@router.get("/audit", response_model=list[AuditRead], summary="Audit trail of decisions on this card, newest first")
def card_audit(card_id: int, session: SessionDep):
    return DecisionService(session).audit_for_card(card_id)
