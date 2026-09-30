"""scoring_mcp: the rubric, score storage and the priority calculation.

The model only ever supplies ratings (see the score_request prompt); weights and totals come from
here so they are consistent. Saving scores stores a Score row; it does not change the card's own
priority_score.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from ai.mcp_servers.common import CardId, RunId, new_server
from core.db import session_scope
from core.errors import InvalidInputError
from core.services.scoring import (
    CriterionScore,
    Priority,
    PriorityLine,
    Rubric,
    ScoringService,
    load_rubric,
)
from core.services.scoring import (
    compute_priority as compute,
)

mcp = new_server(
    "scoring_mcp",
    "Rubric config, score storage and priority calculation. Ratings come from the caller; the maths is done here.",
)


class ScoreInput(BaseModel):
    key: str = Field(description="Criterion key exactly as in get_rubric")
    rating: int = Field(description="Whole number on the rubric scale (see get_rubric), higher = more reason to do it")
    rationale: str = Field("", max_length=500, description="Short reason for this rating")


class SaveScoresResult(BaseModel):
    score_id: int
    card_id: int
    rubric_name: str
    rubric_version: int
    total: float = Field(description="Priority 0-100")
    lines: list[PriorityLine]
    applied_to_card: bool = Field(False, description="Always false: the card's priority_score is unchanged")


@mcp.tool()
def get_rubric() -> Rubric:
    """Get the scoring rubric from its YAML config: name, version, scale, and each criterion's key, label, weight, description and anchors. Read-only."""
    return load_rubric()


@mcp.tool()
def save_scores(
    card_id: CardId,
    scores: Annotated[
        list[ScoreInput],
        Field(min_length=1, description="One entry per rubric criterion, each with a rating and rationale"),
    ],
    run_id: RunId = None,
) -> SaveScoresResult:
    """Store a card's ratings for EVERY rubric criterion and return the computed priority. Rejects missing, unknown or repeated criteria and out-of-range ratings. Does not change the card's own priority_score."""
    with session_scope() as session:
        row, priority = ScoringService(session).save_scores(
            card_id, [CriterionScore(key=s.key, rating=s.rating, rationale=s.rationale) for s in scores], run_id
        )
        return SaveScoresResult(
            score_id=row.id,
            card_id=card_id,
            rubric_name=priority.rubric_name,
            rubric_version=priority.rubric_version,
            total=priority.total,
            lines=priority.lines,
        )


@mcp.tool()
def compute_priority(
    card_id: Annotated[int | None, Field(ge=1, description="Use this card's most recently saved scores")] = None,
    ratings: Annotated[
        dict[str, int] | None, Field(description="Or give ratings directly: {criterion_key: rating}")
    ] = None,
) -> Priority:
    """Compute priority (0-100, weighted average of ratings rescaled) with a per-criterion breakdown. Give exactly one of card_id or ratings. Pure calculation: nothing is stored."""
    if (card_id is None) == (ratings is None):
        raise InvalidInputError("Give exactly one of card_id or ratings", code="invalid_arguments")
    with session_scope() as session:
        if card_id is not None:
            return ScoringService(session).priority_for_card(card_id)
    assert ratings is not None  # exactly one of the two was given
    return compute(load_rubric(), ratings)
