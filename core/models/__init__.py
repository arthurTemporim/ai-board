"""Import every model so Base.metadata is complete (Alembic and create_all rely on this)."""

from core.models.ai import AIRun, Draft, Score
from core.models.board import Board, Column
from core.models.card import Card, CardTag, Tag
from core.models.embedding import CardEmbedding
from core.models.engagement import Comment, Feedback, Vote
from core.models.enums import (
    DraftKind,
    DraftStatus,
    LinkKind,
    LinkStatus,
    RequesterType,
    RunStatus,
)
from core.models.grouping import CardLink, CardProblem, Problem
from core.models.user import User

__all__ = [
    "AIRun",
    "Board",
    "Card",
    "CardEmbedding",
    "CardLink",
    "CardProblem",
    "CardTag",
    "Column",
    "Comment",
    "Draft",
    "DraftKind",
    "DraftStatus",
    "Feedback",
    "LinkKind",
    "LinkStatus",
    "Problem",
    "RequesterType",
    "RunStatus",
    "Score",
    "Tag",
    "User",
    "Vote",
]
