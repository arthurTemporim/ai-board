from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Comment, Feedback, Vote


class CommentRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, comment_id: int) -> Comment | None:
        return self.session.get(Comment, comment_id)

    def add(self, comment: Comment) -> Comment:
        self.session.add(comment)
        self.session.flush()
        return comment

    def delete(self, comment: Comment) -> None:
        self.session.delete(comment)
        self.session.flush()


class FeedbackRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, feedback_id: int) -> Feedback | None:
        return self.session.get(Feedback, feedback_id)

    def add(self, feedback: Feedback) -> Feedback:
        self.session.add(feedback)
        self.session.flush()
        return feedback

    def delete(self, feedback: Feedback) -> None:
        self.session.delete(feedback)
        self.session.flush()


class VoteRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, card_id: int, user_id: int) -> Vote | None:
        return self.session.scalar(
            select(Vote).where(Vote.card_id == card_id, Vote.user_id == user_id)
        )

    def add(self, vote: Vote) -> Vote:
        self.session.add(vote)
        self.session.flush()
        return vote

    def delete(self, vote: Vote) -> None:
        self.session.delete(vote)
        self.session.flush()
