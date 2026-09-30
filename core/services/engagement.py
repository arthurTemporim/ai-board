from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.errors import ConflictError, ForbiddenError, NotFoundError
from core.models import Card, Comment, Feedback, Vote
from core.repositories.boards import UserRepository
from core.repositories.cards import CardRepository
from core.repositories.engagement import CommentRepository, FeedbackRepository, VoteRepository


@dataclass
class VoteSummary:
    card_id: int
    vote_count: int
    voted: bool


class _CardScoped:
    def __init__(self, session: Session):
        self.session = session
        self.cards = CardRepository(session)
        self.users = UserRepository(session)

    def _card(self, card_id: int) -> Card:
        card = self.cards.get(card_id)
        if card is None:
            raise NotFoundError(f"Card {card_id} not found")
        return card

    def _refresh_counts(self, card: Card) -> None:
        self.session.expire(card, ["vote_count", "comment_count"])


class VoteService(_CardScoped):
    def __init__(self, session: Session):
        super().__init__(session)
        self.votes = VoteRepository(session)

    def summary(self, card_id: int, user_id: int | None = None) -> VoteSummary:
        card = self._card(card_id)
        voted = user_id is not None and self.votes.get(card_id, user_id) is not None
        return VoteSummary(card_id, card.vote_count, voted)

    def add_vote(self, card_id: int, user_id: int) -> VoteSummary:
        card = self._card(card_id)
        if self.votes.get(card_id, user_id) is not None:
            raise ConflictError("You have already voted for this card", code="already_voted")
        try:
            # Savepoint: a concurrent duplicate hits the unique constraint without
            # poisoning the outer transaction.
            with self.session.begin_nested():
                self.votes.add(Vote(card_id=card_id, user_id=user_id))
        except IntegrityError as exc:
            raise ConflictError(
                "You have already voted for this card", code="already_voted"
            ) from exc
        self._refresh_counts(card)
        return VoteSummary(card_id, card.vote_count, True)

    def remove_vote(self, card_id: int, user_id: int) -> VoteSummary:
        card = self._card(card_id)
        vote = self.votes.get(card_id, user_id)
        if vote is None:
            raise NotFoundError("You have not voted for this card", code="vote_not_found")
        self.votes.delete(vote)
        self._refresh_counts(card)
        return VoteSummary(card_id, card.vote_count, False)


class CommentService(_CardScoped):
    def __init__(self, session: Session):
        super().__init__(session)
        self.comments = CommentRepository(session)

    def list_comments(self, card_id: int) -> list[Comment]:
        self._card(card_id)
        return self.cards.comments(card_id)

    def add_comment(self, card_id: int, author_id: int, body: str) -> Comment:
        card = self._card(card_id)
        comment = self.comments.add(Comment(card_id=card_id, author_id=author_id, body=body))
        self._refresh_counts(card)
        self.session.refresh(comment)
        return comment

    def update_comment(self, comment_id: int, actor_id: int, body: str) -> Comment:
        comment = self._owned(comment_id, actor_id)
        comment.body = body
        self.session.flush()
        return comment

    def delete_comment(self, comment_id: int, actor_id: int) -> None:
        comment = self._owned(comment_id, actor_id)
        card = self._card(comment.card_id)
        self.comments.delete(comment)
        self._refresh_counts(card)

    def _owned(self, comment_id: int, actor_id: int) -> Comment:
        comment = self.comments.get(comment_id)
        if comment is None:
            raise NotFoundError(f"Comment {comment_id} not found")
        if comment.author_id != actor_id:
            raise ForbiddenError("Only the author can change this comment")
        return comment


class FeedbackService(_CardScoped):
    def __init__(self, session: Session):
        super().__init__(session)
        self.feedback = FeedbackRepository(session)

    def list_feedback(self, card_id: int) -> list[Feedback]:
        self._card(card_id)
        return self.cards.feedback(card_id)

    def add_feedback(
        self, card_id: int, source: str, quote: str, customer_ref: str | None = None
    ) -> Feedback:
        self._card(card_id)
        return self.feedback.add(
            Feedback(card_id=card_id, source=source, quote=quote, customer_ref=customer_ref)
        )

    def update_feedback(self, feedback_id: int, changes: dict) -> Feedback:
        entry = self._get(feedback_id)
        for name, value in changes.items():
            setattr(entry, name, value)
        self.session.flush()
        return entry

    def delete_feedback(self, feedback_id: int) -> None:
        self.feedback.delete(self._get(feedback_id))

    def _get(self, feedback_id: int) -> Feedback:
        entry = self.feedback.get(feedback_id)
        if entry is None:
            raise NotFoundError(f"Feedback {feedback_id} not found")
        return entry
