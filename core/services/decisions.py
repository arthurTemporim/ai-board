"""Human decisions about AI suggestions: list them, accept them (which applies the change) or reject them.

Everything the AI produces is a suggestion (a Draft, or a CardLink / CardProblem with status
"suggested"). This module is the only place a suggestion turns into a change to the board, and each
decision writes an AuditEntry in the same transaction, so the audit trail cannot disagree with the data.

Suggestion ids are strings, because suggestions live in three tables:
    draft:<id>              a Draft (tag, score, brief, comms, comment, field edit, new problem, analysis)
    link:<id>               a CardLink proposed as duplicate or related
    problem:<card>:<problem> a card proposed to belong to an existing problem
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.errors import ConflictError, InvalidInputError, NotFoundError
from core.models import (
    AuditEntry,
    Card,
    CardLink,
    CardProblem,
    Comment,
    Draft,
    DraftKind,
    DraftStatus,
    LinkKind,
    LinkStatus,
    Problem,
    RequesterType,
    Score,
    Tag,
    Vote,
)
from core.models.types import utcnow
from core.repositories.cards import CardRepository, TagRepository
from core.repositories.suggestions import ProblemRepository
from core.services.cards import CardService
from core.services.scoring import compute_priority, load_rubric

MIN_REASON_CHARS = 3
DEFAULT_TAG_COLOR = "#6B7280"

# What a person may change before accepting, per suggestion type.
EDITABLE: dict[str, set[str]] = {
    "tag": {"name"},
    "brief": {"summary"},
    "comment": {"body"},
    "comms": {"subject", "body"},
    "new_problem": {"title", "statement"},
    "field_edit": {"title", "description", "requester_type", "progress", "priority_score"},
}


@dataclass
class SuggestionView:
    id: str
    # duplicate | related | problem | new_problem | tag | score | brief | comms | comment | field_edit | analysis
    type: str
    status: str  # pending | accepted | applied | rejected | confirmed
    confidence: float | None
    rationale: str | None
    data: dict[str, Any]
    created_at: datetime
    run_id: int | None = None
    editable: list[str] = field(default_factory=list)


@dataclass
class DecisionResult:
    suggestion_id: str
    status: str
    message: str
    effects: dict[str, Any] = field(default_factory=dict)
    audit_id: int | None = None


class FieldEdits(BaseModel):
    """What a field-edit suggestion (or a person editing one) may set on a card. Same limits as the API."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    description: Annotated[str, StringConstraints(max_length=10000)] | None = None
    requester_type: RequesterType | None = None
    progress: int | None = Field(None, ge=0, le=100)
    priority_score: float | None = Field(None, ge=0, le=100)


def _clamp(text: str, limit: int) -> str:
    return text.strip()[:limit]


def _pick(edits: dict[str, Any], payload: dict[str, Any], key: str) -> str:
    """The edited value if the person sent one (even an empty one, which is then rejected by the
    caller), else the AI's value."""
    return str(edits[key] if key in edits else payload.get(key) or "").strip()


def parse_id(sid: str) -> tuple[str, tuple[int, ...]]:
    kind, _, rest = sid.partition(":")
    try:
        numbers = tuple(int(part) for part in rest.split(":"))
    except ValueError:
        numbers = ()
    expected = {"draft": 1, "link": 1, "problem": 2}.get(kind)
    if expected is None or len(numbers) != expected:
        raise InvalidInputError(
            f"'{sid}' is not a suggestion id (draft:N, link:N or problem:CARD:PROBLEM)", code="bad_suggestion_id"
        )
    return kind, numbers


class DecisionService:
    def __init__(self, session: Session):
        self.session = session
        self.cards = CardRepository(session)
        self.tags = TagRepository(session)
        self.problems = ProblemRepository(session)

    # ---- helpers ------------------------------------------------------------------------
    def _card(self, card_id: int) -> Card:
        card = self.cards.get(card_id)
        if card is None:
            raise NotFoundError(f"Card {card_id} not found")
        return card

    def _audit(self, action: str, card_id: int | None, actor_id: int, detail: dict[str, Any]) -> AuditEntry:
        entry = AuditEntry(action=action, card_id=card_id, actor_id=actor_id, detail=detail)
        self.session.add(entry)
        self.session.flush()
        return entry

    @staticmethod
    def _check_edits(kind: str, edits: dict[str, Any] | None) -> dict[str, Any]:
        edits = edits or {}
        unknown = sorted(set(edits) - EDITABLE.get(kind, set()))
        if unknown:
            allowed = sorted(EDITABLE.get(kind, set())) or "nothing"
            raise InvalidInputError(
                f"Cannot edit {unknown} on a {kind} suggestion; editable: {allowed}", code="not_editable"
            )
        return edits

    @staticmethod
    def _decide_draft(draft: Draft, status: DraftStatus, actor_id: int) -> None:
        draft.status, draft.decided_by_id, draft.decided_at = status, actor_id, utcnow()

    # ---- listing ------------------------------------------------------------------------
    def list(self, card_id: int, include_decided: bool = False) -> list[SuggestionView]:
        card = self._card(card_id)
        views: list[SuggestionView] = []

        links = self.session.scalars(
            select(CardLink)
            .where((CardLink.card_id == card_id) | (CardLink.other_card_id == card_id))
            .order_by(CardLink.id)
        )
        for link in links:
            if link.status == LinkStatus.suggested or (include_decided and link.confidence is not None):
                views.append(self._link_view(card, link))

        attachments = self.session.scalars(select(CardProblem).where(CardProblem.card_id == card_id))
        for item in attachments:
            if item.status == LinkStatus.suggested or (include_decided and item.confidence is not None):
                problem = self.problems.get(item.problem_id)
                views.append(
                    SuggestionView(
                        id=f"problem:{item.card_id}:{item.problem_id}",
                        type="problem",
                        status=str(item.status),
                        confidence=item.confidence,
                        rationale=item.rationale,
                        data={"problem_id": item.problem_id, "problem_title": problem.title if problem else "?"},
                        created_at=item.created_at,
                    )
                )

        stmt = select(Draft).where(Draft.card_id == card_id, Draft.kind != DraftKind.link).order_by(Draft.id)
        if not include_decided:
            stmt = stmt.where(Draft.status == DraftStatus.pending)
        for draft in self.session.scalars(stmt):
            views.append(self._draft_view(card, draft))

        order = [
            "duplicate",
            "related",
            "problem",
            "new_problem",
            "tag",
            "score",
            "brief",
            "comms",
            "comment",
            "field_edit",
            "analysis",
        ]
        views.sort(key=lambda v: (order.index(v.type) if v.type in order else 99, v.created_at, v.id))
        return views

    def _link_view(self, card: Card, link: CardLink) -> SuggestionView:
        other_id = link.other_card_id if link.card_id == card.id else link.card_id
        other = self._card(other_id)
        data: dict[str, Any] = {
            "other_card_id": other.id,
            "other_title": other.title,
            "other_column_id": other.column_id,
        }
        if link.kind == LinkKind.duplicate:
            primary, duplicate = self._merge_roles(link)
            data["merge"] = {
                "primary_card_id": primary,
                "duplicate_card_id": duplicate,
                "votes_on_duplicate": self._vote_count(duplicate),
            }
        return SuggestionView(
            id=f"link:{link.id}",
            type=str(link.kind),
            status=str(link.status),
            confidence=link.confidence,
            rationale=link.rationale,
            data=data,
            created_at=link.created_at,
        )

    def _draft_view(self, card: Card, draft: Draft) -> SuggestionView:
        payload = dict(draft.payload)
        kind = str(draft.kind)
        rationale = payload.get("rationale") or payload.get("reason")
        data: dict[str, Any] = payload
        stype = kind
        if kind == "problem":
            stype = "new_problem"
            data = {"title": payload.get("new_problem_title"), "statement": payload.get("new_problem_statement")}
        elif kind == "score":
            data = self._score_data(payload)
            rationale = None
        elif kind == "field_edit":
            data = {**payload, "current_version": card.version, "stale": payload.get("base_version") != card.version}
        elif kind == "analysis":
            rationale = payload.get("rationale")
        view = SuggestionView(
            id=f"draft:{draft.id}",
            type=stype,
            status=str(draft.status),
            confidence=draft.confidence,
            rationale=rationale,
            data=data,
            created_at=draft.created_at,
            run_id=draft.run_id,
            editable=sorted(EDITABLE.get(stype, set())),
        )
        return view

    def _score_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        score = self.session.get(Score, payload.get("score_id")) if payload.get("score_id") else None
        lines = []
        for line in payload.get("lines", []):
            entry = dict(line)
            if score is not None:
                entry["rationale"] = score.per_criterion.get(line["key"], {}).get("rationale", "")
            lines.append(entry)
        return {
            "score_id": payload.get("score_id"),
            "total": payload.get("total"),
            "rubric": payload.get("rubric"),
            "lines": lines,
        }

    def _vote_count(self, card_id: int) -> int:
        return len(list(self.session.scalars(select(Vote.id).where(Vote.card_id == card_id))))

    def _merge_roles(self, link: CardLink) -> tuple[int, int]:
        """(primary, duplicate): the merge proposal's choice if there is one, else the older card."""
        for draft in self.session.scalars(
            select(Draft).where(Draft.kind == DraftKind.link, Draft.status == DraftStatus.pending)
        ):
            p = draft.payload
            if p.get("action") == "merge" and {p.get("keep_card_id"), p.get("merge_card_id")} == {
                link.card_id,
                link.other_card_id,
            }:
                return int(p["keep_card_id"]), int(p["merge_card_id"])
        return link.card_id, link.other_card_id  # stored with card_id < other_card_id, so the older card first

    def _merge_drafts(self, link: CardLink) -> list[Draft]:
        pair = {link.card_id, link.other_card_id}
        return [
            d
            for d in self.session.scalars(
                select(Draft).where(Draft.kind == DraftKind.link, Draft.status == DraftStatus.pending)
            )
            if d.payload.get("action") == "merge"
            and {d.payload.get("keep_card_id"), d.payload.get("merge_card_id")} == pair
        ]

    # ---- accept -------------------------------------------------------------------------
    def accept(
        self,
        card_id: int,
        sid: str,
        actor_id: int,
        *,
        edits: dict[str, Any] | None = None,
        override: dict[str, Any] | None = None,
    ) -> DecisionResult:
        self._card(card_id)
        kind, ids = parse_id(sid)
        if kind == "link":
            self._no_edits(edits, override)
            return self._accept_link(card_id, ids[0], actor_id, sid)
        if kind == "problem":
            self._no_edits(edits, override)
            return self._accept_attachment(card_id, ids, actor_id, sid)
        draft = self._pending_draft(card_id, ids[0])
        if override is not None and draft.kind != DraftKind.score:
            raise InvalidInputError("Only score suggestions can be overridden", code="not_overridable")
        handler = {
            DraftKind.tag: self._accept_tag,
            DraftKind.score: self._accept_score,
            DraftKind.brief: self._accept_brief,
            DraftKind.comment: self._accept_comment,
            DraftKind.comms: self._accept_comms,
            DraftKind.field_edit: self._accept_field_edit,
            DraftKind.problem: self._accept_new_problem,
            DraftKind.analysis: self._accept_ack,
        }.get(draft.kind)
        if handler is None:
            raise InvalidInputError("Accept the duplicate/related link instead of its merge proposal", code="use_link")
        return handler(card_id, draft, actor_id, sid, edits, override)

    @staticmethod
    def _no_edits(edits: Any, override: Any) -> None:
        if edits or override:
            raise InvalidInputError("This suggestion cannot be edited", code="not_editable")

    def _pending_draft(self, card_id: int, draft_id: int) -> Draft:
        draft = self.session.get(Draft, draft_id)
        if draft is None or draft.card_id != card_id:
            raise NotFoundError(f"Suggestion draft:{draft_id} not found on card {card_id}")
        if draft.status != DraftStatus.pending:
            raise ConflictError(f"Suggestion draft:{draft_id} was already {draft.status}", code="already_decided")
        return draft

    def _pending_link(self, card_id: int, link_id: int) -> CardLink:
        link = self.session.get(CardLink, link_id)
        if link is None or card_id not in (link.card_id, link.other_card_id):
            raise NotFoundError(f"Suggestion link:{link_id} not found on card {card_id}")
        if link.status != LinkStatus.suggested:
            raise ConflictError(f"Suggestion link:{link_id} was already {link.status}", code="already_decided")
        return link

    def _accepted(
        self, sid: str, card_id: int, actor_id: int, stype: str, detail: dict[str, Any], edited: bool
    ) -> AuditEntry:
        return self._audit(
            "suggestion_accepted", card_id, actor_id, {"suggestion": sid, "type": stype, "edited": edited, **detail}
        )

    def _accept_link(self, card_id: int, link_id: int, actor_id: int, sid: str) -> DecisionResult:
        link = self._pending_link(card_id, link_id)
        link.status = LinkStatus.confirmed
        effects: dict[str, Any] = {}
        if link.kind == LinkKind.duplicate:
            primary, duplicate = self._merge_roles(link)
            moved, dropped = self._merge_votes(primary, duplicate)
            for draft in self._merge_drafts(link):
                self._decide_draft(draft, DraftStatus.applied, actor_id)
            effects = {
                "primary_card_id": primary,
                "duplicate_card_id": duplicate,
                "votes_moved": len(moved),
                "votes_already_on_primary": len(dropped),
            }
            self._audit(
                "duplicate_merged",
                card_id,
                actor_id,
                {
                    "suggestion": sid,
                    **effects,
                    "moved_user_ids": moved,
                    "dropped_user_ids": dropped,
                    "link_id": link.id,
                },
            )
        entry = self._accepted(
            sid, card_id, actor_id, str(link.kind), {"link_id": link.id, "confidence": link.confidence}, False
        )
        message = (
            f"Merged: {effects['votes_moved']} vote(s) moved to card {effects['primary_card_id']}; "
            "the cards stay linked"
            if effects
            else "Marked as related"
        )
        return DecisionResult(sid, "confirmed", message, effects, entry.id)

    def _merge_votes(self, primary_id: int, duplicate_id: int) -> tuple[list[int], list[int]]:
        have = set(self.session.scalars(select(Vote.user_id).where(Vote.card_id == primary_id)))
        moved: list[int] = []
        dropped: list[int] = []
        for vote in list(self.session.scalars(select(Vote).where(Vote.card_id == duplicate_id).order_by(Vote.id))):
            if vote.user_id in have:
                dropped.append(vote.user_id)
                self.session.delete(vote)
            else:
                vote.card_id = primary_id
                have.add(vote.user_id)
                moved.append(vote.user_id)
        self.session.flush()
        for card_id in (primary_id, duplicate_id):
            card = self.cards.get(card_id)
            if card is not None:
                self.session.expire(card, ["vote_count"])
        return moved, dropped

    def _accept_attachment(self, card_id: int, ids: tuple[int, ...], actor_id: int, sid: str) -> DecisionResult:
        item = self._pending_attachment(card_id, ids)
        item.status = LinkStatus.confirmed
        entry = self._accepted(
            sid, card_id, actor_id, "problem", {"problem_id": item.problem_id, "confidence": item.confidence}, False
        )
        return DecisionResult(sid, "confirmed", "Card added to the problem", {"problem_id": item.problem_id}, entry.id)

    def _pending_attachment(self, card_id: int, ids: tuple[int, ...]) -> CardProblem:
        item = self.problems.attachment(ids[0], ids[1])
        if item is None or item.card_id != card_id:
            raise NotFoundError(f"Suggestion problem:{ids[0]}:{ids[1]} not found on card {card_id}")
        if item.status != LinkStatus.suggested:
            raise ConflictError(
                f"Suggestion problem:{ids[0]}:{ids[1]} was already {item.status}", code="already_decided"
            )
        return item

    def _accept_tag(self, card_id, draft, actor_id, sid, edits, override) -> DecisionResult:
        edits = self._check_edits("tag", edits)
        card = self._card(card_id)
        name = _pick(edits, draft.payload, "name")
        if not name or len(name) > 40:
            raise InvalidInputError("Tag name must be 1-40 characters", code="invalid_tag")
        tag = self.tags.by_name(card.board_id, name)
        created = tag is None
        if tag is None:
            tag = self.tags.add(Tag(board_id=card.board_id, name=name, color=DEFAULT_TAG_COLOR))
        if all(t.id != tag.id for t in card.tags):
            card.tags = [*card.tags, tag]
            card.version += 1
            card.updated_at = utcnow()
        self._decide_draft(draft, DraftStatus.applied, actor_id)
        entry = self._accepted(
            sid, card_id, actor_id, "tag", {"tag_id": tag.id, "tag": tag.name, "tag_created": created}, bool(edits)
        )
        return DecisionResult(
            sid, "applied", f"Tag '{tag.name}' added", {"tag_id": tag.id, "tag_created": created}, entry.id
        )

    def _accept_score(self, card_id, draft, actor_id, sid, edits, override) -> DecisionResult:
        self._no_edits(edits, None)
        card = self._card(card_id)
        before = card.priority_score
        score = self.session.get(Score, draft.payload.get("score_id"))
        if score is None:
            raise NotFoundError("The score behind this suggestion no longer exists")
        if not override:
            card.priority_score = score.total
            card.version += 1
            card.updated_at = utcnow()
            self._decide_draft(draft, DraftStatus.applied, actor_id)
            entry = self._accepted(
                sid, card_id, actor_id, "score", {"score_id": score.id, "before": before, "after": score.total}, False
            )
            return DecisionResult(
                sid, "applied", f"Priority set to {score.total:g}", {"priority_score": score.total}, entry.id
            )

        reason = str(override.get("reason") or "").strip()
        if len(reason) < MIN_REASON_CHARS:
            raise InvalidInputError("A reason is required to override a score", code="reason_required")
        changes = override.get("ratings") or {}
        if not isinstance(changes, dict) or not changes:
            raise InvalidInputError("Give at least one rating to override", code="invalid_ratings")
        ratings = {k: int(v["rating"]) for k, v in score.per_criterion.items()}
        unknown = sorted(set(changes) - set(ratings))
        if unknown:
            raise InvalidInputError(f"Unknown criteria {unknown}", code="invalid_ratings")
        rubric = load_rubric()
        if (rubric.name, rubric.version) != (score.rubric_name, score.rubric_version):
            raise ConflictError(
                "The rubric changed since this score was made; run scoring again", code="rubric_changed"
            )
        new_ratings = {**ratings, **{k: int(v) for k, v in changes.items()}}
        priority = compute_priority(rubric, new_ratings)  # validates the range of every rating
        per_criterion = {
            key: {
                "rating": new_ratings[key],
                "weight": score.per_criterion[key].get("weight"),
                "rationale": (
                    f"Overridden: {reason}" if key in changes else score.per_criterion[key].get("rationale", "")
                ),
            }
            for key in new_ratings
        }
        self.session.add(
            Score(
                card_id=card_id,
                run_id=draft.run_id,
                rubric_name=score.rubric_name,
                rubric_version=score.rubric_version,
                per_criterion=per_criterion,
                total=priority.total,
            )
        )
        card.priority_score = priority.total
        card.version += 1
        card.updated_at = utcnow()
        self._decide_draft(draft, DraftStatus.applied, actor_id)
        diff = {k: [ratings[k], new_ratings[k]] for k in changes if ratings[k] != new_ratings[k]}
        if not diff:
            raise InvalidInputError("The override does not change any rating", code="invalid_ratings")
        entry = self._audit(
            "score_override",
            card_id,
            actor_id,
            {
                "suggestion": sid,
                "reason": reason,
                "changes": diff,
                "ai_total": score.total,
                "before": before,
                "after": priority.total,
            },
        )
        return DecisionResult(
            sid,
            "applied",
            f"Priority set to {priority.total:g} (AI suggested {score.total:g})",
            {"priority_score": priority.total, "overridden": diff},
            entry.id,
        )

    def _accept_brief(self, card_id, draft, actor_id, sid, edits, override) -> DecisionResult:
        edits = self._check_edits("brief", edits)
        p = draft.payload
        summary = _pick(edits, p, "summary")
        if not summary:
            raise InvalidInputError("The summary cannot be empty", code="invalid_brief")
        lines = [f"Decision brief: {summary}", f"Recommendation: {p.get('recommendation', '?')}"]
        if p.get("risks"):
            lines.append("Risks: " + "; ".join(p["risks"]))
        if p.get("open_questions"):
            lines.append("Open questions: " + "; ".join(p["open_questions"]))
        comment = self._post_comment(card_id, actor_id, "\n".join(lines))
        self._decide_draft(draft, DraftStatus.applied, actor_id)
        entry = self._accepted(sid, card_id, actor_id, "brief", {"comment_id": comment.id}, bool(edits))
        return DecisionResult(sid, "applied", "Brief posted as a comment", {"comment_id": comment.id}, entry.id)

    def _accept_comment(self, card_id, draft, actor_id, sid, edits, override) -> DecisionResult:
        edits = self._check_edits("comment", edits)
        body = _pick(edits, draft.payload, "body")
        if not body:
            raise InvalidInputError("The comment cannot be empty", code="invalid_comment")
        comment = self._post_comment(card_id, actor_id, body)
        self._decide_draft(draft, DraftStatus.applied, actor_id)
        entry = self._accepted(sid, card_id, actor_id, "comment", {"comment_id": comment.id}, bool(edits))
        return DecisionResult(sid, "applied", "Comment posted", {"comment_id": comment.id}, entry.id)

    def _post_comment(self, card_id: int, actor_id: int, body: str) -> Comment:
        comment = Comment(card_id=card_id, author_id=actor_id, body=body[:5000])
        self.session.add(comment)
        self.session.flush()
        self.session.expire(self._card(card_id), ["comment_count"])
        return comment

    def _accept_comms(self, card_id, draft, actor_id, sid, edits, override) -> DecisionResult:
        edits = self._check_edits("comms", edits)
        if edits:
            limits = {"subject": 120, "body": 2000}
            cleaned = {k: _clamp(str(v), limits[k]) for k, v in edits.items()}
            if not all(cleaned.values()):
                raise InvalidInputError("The subject and message cannot be empty", code="invalid_draft")
            draft.payload = {**draft.payload, **cleaned}
        self._decide_draft(draft, DraftStatus.accepted, actor_id)
        entry = self._accepted(
            sid, card_id, actor_id, "comms", {"audience": draft.payload.get("audience")}, bool(edits)
        )
        return DecisionResult(
            sid, "accepted", "Draft approved. Nothing was sent: copy it into your own channel", {}, entry.id
        )

    def _accept_ack(self, card_id, draft, actor_id, sid, edits, override) -> DecisionResult:
        self._no_edits(edits, None)
        self._decide_draft(draft, DraftStatus.accepted, actor_id)
        entry = self._accepted(sid, card_id, actor_id, "analysis", {}, False)
        return DecisionResult(sid, "accepted", "Analysis acknowledged", {}, entry.id)

    def _accept_field_edit(self, card_id, draft, actor_id, sid, edits, override) -> DecisionResult:
        edits = self._check_edits("field_edit", edits)
        card = self._card(card_id)
        if draft.payload.get("base_version") != card.version:
            raise ConflictError(
                "The card changed since this edit was suggested; reject it and run triage again",
                code="version_conflict",
            )
        try:
            fields = FieldEdits.model_validate({**draft.payload.get("fields", {}), **edits}).model_dump(
                exclude_unset=True
            )
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            raise InvalidInputError(f"Invalid card fields: {problems}", code="invalid_fields") from exc
        before = {k: getattr(card, k) for k in fields}
        CardService(self.session).update_card(card_id, fields, expected_version=card.version)
        self._decide_draft(draft, DraftStatus.applied, actor_id)
        entry = self._accepted(
            sid, card_id, actor_id, "field_edit", {"before": _jsonable(before), "after": _jsonable(fields)}, bool(edits)
        )
        return DecisionResult(sid, "applied", "Card updated", {"fields": sorted(fields)}, entry.id)

    def _accept_new_problem(self, card_id, draft, actor_id, sid, edits, override) -> DecisionResult:
        edits = self._check_edits("new_problem", edits)
        card = self._card(card_id)
        title = _pick(edits, {"title": draft.payload.get("new_problem_title")}, "title")
        statement = _pick(edits, {"statement": draft.payload.get("new_problem_statement")}, "statement")[:2000]
        if not title or len(title) > 200:
            raise InvalidInputError("The problem needs a title of 1-200 characters", code="invalid_problem")
        problem = Problem(board_id=card.board_id, title=title, statement=statement)
        self.session.add(problem)
        self.session.flush()
        self.session.add(
            CardProblem(
                card_id=card_id, problem_id=problem.id, status=LinkStatus.confirmed, confidence=draft.confidence
            )
        )
        self._decide_draft(draft, DraftStatus.applied, actor_id)
        entry = self._accepted(
            sid, card_id, actor_id, "new_problem", {"problem_id": problem.id, "title": title}, bool(edits)
        )
        return DecisionResult(
            sid, "applied", f"Problem '{title}' created and the card attached", {"problem_id": problem.id}, entry.id
        )

    # ---- reject -------------------------------------------------------------------------
    def reject(self, card_id: int, sid: str, actor_id: int, *, reason: str | None = None) -> DecisionResult:
        self._card(card_id)
        kind, ids = parse_id(sid)
        reason = (reason or "").strip() or None
        stype: str
        detail: dict[str, Any] = {}
        if kind == "link":
            link = self._pending_link(card_id, ids[0])
            link.status = LinkStatus.rejected
            for draft in self._merge_drafts(link):
                self._decide_draft(draft, DraftStatus.rejected, actor_id)
            stype, detail = str(link.kind), {"link_id": link.id, "confidence": link.confidence}
        elif kind == "problem":
            item = self._pending_attachment(card_id, ids)
            item.status = LinkStatus.rejected
            stype, detail = "problem", {"problem_id": item.problem_id, "confidence": item.confidence}
        else:
            draft = self._pending_draft(card_id, ids[0])
            if draft.kind == DraftKind.link:
                raise InvalidInputError(
                    "Reject the duplicate/related link instead of its merge proposal", code="use_link"
                )
            self._decide_draft(draft, DraftStatus.rejected, actor_id)
            stype = "new_problem" if draft.kind == DraftKind.problem else str(draft.kind)
            detail = {"confidence": draft.confidence}
        if reason:
            detail["reason"] = reason
        entry = self._audit("suggestion_rejected", card_id, actor_id, {"suggestion": sid, "type": stype, **detail})
        return DecisionResult(sid, "rejected", "Suggestion rejected", {}, entry.id)

    # ---- audit --------------------------------------------------------------------------
    def audit_for_card(self, card_id: int, limit: int = 100) -> list[AuditEntry]:
        self._card(card_id)
        return list(
            self.session.scalars(
                select(AuditEntry).where(AuditEntry.card_id == card_id).order_by(AuditEntry.id.desc()).limit(limit)
            )
        )


def _jsonable(values: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if not isinstance(v, str | int | float | bool | type(None)) else v) for k, v in values.items()}
