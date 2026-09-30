"""AI output storage: drafts (suggestions), link and problem suggestions.

Everything here records a *proposal*. Nothing changes a card's own fields; that only happens
when a person accepts a suggestion through the API.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.errors import InvalidInputError, NotFoundError
from core.models import (
    CardLink,
    CardProblem,
    Draft,
    DraftKind,
    DraftStatus,
    LinkKind,
    LinkStatus,
    Problem,
)
from core.repositories.cards import CardRepository
from core.repositories.suggestions import DraftRepository, LinkRepository, ProblemRepository


def _check_confidence(confidence: float | None) -> None:
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise InvalidInputError("confidence must be between 0 and 1", code="invalid_confidence")


class SuggestionService:
    def __init__(self, session: Session):
        self.session = session
        self.cards = CardRepository(session)
        self.drafts = DraftRepository(session)
        self.links = LinkRepository(session)
        self.problems = ProblemRepository(session)

    def _card(self, card_id: int):
        card = self.cards.get(card_id)
        if card is None:
            raise NotFoundError(f"Card {card_id} not found")
        return card

    # ---- drafts -------------------------------------------------------------------------
    def save_draft(
        self,
        card_id: int,
        kind: DraftKind,
        payload: dict,
        *,
        confidence: float | None = None,
        run_id: int | None = None,
    ) -> tuple[Draft, bool]:
        """Store a pending draft; an identical pending one is returned instead of a duplicate.

        Returns (draft, created).
        """
        self._card(card_id)
        _check_confidence(confidence)
        if run_id is not None and not self.drafts.run_exists(run_id):
            raise NotFoundError(f"AI run {run_id} not found")
        for existing in self.drafts.list(card_id, kind=kind, status=DraftStatus.pending, limit=200):
            if existing.payload == payload:
                return existing, False
        draft = self.drafts.add(
            Draft(card_id=card_id, run_id=run_id, kind=kind, payload=payload, confidence=confidence)
        )
        return draft, True

    def list_drafts(
        self,
        card_id: int,
        kind: DraftKind | None = None,
        status: DraftStatus | None = None,
        limit: int = 50,
    ) -> list[Draft]:
        self._card(card_id)
        return self.drafts.list(card_id, kind, status, limit)

    # ---- links --------------------------------------------------------------------------
    def suggest_link(
        self,
        card_id: int,
        other_card_id: int,
        kind: LinkKind,
        *,
        confidence: float | None = None,
        rationale: str | None = None,
    ) -> tuple[CardLink, bool]:
        """Propose that two cards are duplicates or related. Returns (link, created).

        Re-proposing an existing suggestion refreshes its kind, confidence and rationale; a link a
        person already confirmed is never touched.
        """
        if card_id == other_card_id:
            raise InvalidInputError("A card cannot be linked to itself", code="self_link")
        card, other = self._card(card_id), self._card(other_card_id)
        if card.board_id != other.board_id:
            raise InvalidInputError("Cards are on different boards", code="wrong_board")
        _check_confidence(confidence)
        low, high = sorted((card_id, other_card_id))
        link = self.links.pair(low, high)
        if link is None:
            link = self.links.add(
                CardLink(
                    card_id=low,
                    other_card_id=high,
                    kind=kind,
                    status=LinkStatus.suggested,
                    confidence=confidence,
                    rationale=rationale,
                )
            )
            return link, True
        if link.status == LinkStatus.suggested:
            link.kind, link.confidence, link.rationale = kind, confidence, rationale
            self.session.flush()
        return link, False

    # ---- problems -----------------------------------------------------------------------
    def list_problems(self, board_id: int) -> list[tuple[Problem, int]]:
        return self.problems.for_board(board_id)

    def suggest_problem(
        self,
        card_id: int,
        problem_id: int,
        *,
        confidence: float | None = None,
        rationale: str | None = None,
    ) -> tuple[CardProblem, bool]:
        """Propose that a card belongs to an existing problem. Returns (attachment, created)."""
        card = self._card(card_id)
        problem = self.problems.get(problem_id)
        if problem is None:
            raise NotFoundError(f"Problem {problem_id} not found")
        if problem.board_id != card.board_id:
            raise InvalidInputError("Problem is on a different board", code="wrong_board")
        _check_confidence(confidence)
        attachment = self.problems.attachment(card_id, problem_id)
        if attachment is None:
            attachment = self.problems.add_attachment(
                CardProblem(
                    card_id=card_id,
                    problem_id=problem_id,
                    status=LinkStatus.suggested,
                    confidence=confidence,
                    rationale=rationale,
                )
            )
            return attachment, True
        if attachment.status == LinkStatus.suggested:
            attachment.confidence, attachment.rationale = confidence, rationale
            self.session.flush()
        return attachment, False
