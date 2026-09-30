"""Rubric configuration and priority maths. Deterministic; the model only supplies ratings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from core.errors import InvalidInputError, NotFoundError
from core.models import Score
from core.repositories.cards import CardRepository
from core.repositories.suggestions import DraftRepository, ScoreRepository

DEFAULT_RUBRIC_PATH = Path(__file__).resolve().parents[2] / "config" / "rubric.yaml"


class Scale(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int
    max: int

    @model_validator(mode="after")
    def _ordered(self) -> Scale:
        if self.min >= self.max:
            raise ValueError("scale.min must be lower than scale.max")
        return self


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    label: str
    weight: float = Field(gt=0)
    description: str = ""
    anchors: dict[int, str] = Field(default_factory=dict)


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: int = Field(ge=1)
    scale: Scale
    criteria: list[Criterion] = Field(min_length=1)

    @field_validator("criteria")
    @classmethod
    def _unique_keys(cls, criteria: list[Criterion]) -> list[Criterion]:
        keys = [c.key for c in criteria]
        if len(set(keys)) != len(keys):
            raise ValueError("criteria keys must be unique")
        return criteria

    @model_validator(mode="after")
    def _anchors_on_scale(self) -> Rubric:
        for criterion in self.criteria:
            bad = [a for a in criterion.anchors if not self.scale.min <= a <= self.scale.max]
            if bad:
                raise ValueError(f"criterion '{criterion.key}' has anchors outside the scale: {bad}")
        return self


class CriterionScore(BaseModel):
    key: str
    rating: int
    rationale: str = ""


class PriorityLine(BaseModel):
    key: str
    rating: int
    weight: float
    normalized: float  # 0..1 position of the rating on the scale
    points: float  # share of the 0-100 priority this criterion contributes


class Priority(BaseModel):
    rubric_name: str
    rubric_version: int
    total: float  # 0..100
    lines: list[PriorityLine]


def rubric_path() -> Path:
    return Path(os.environ.get("RUBRIC_PATH") or DEFAULT_RUBRIC_PATH)


@lru_cache(maxsize=8)
def _load(path: str, mtime_ns: int) -> Rubric:  # mtime is part of the key so edits are picked up
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InvalidInputError(f"Cannot read rubric {path}: {exc}", code="bad_rubric") from exc
    try:
        return Rubric.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise InvalidInputError(f"Invalid rubric {path}: {problems}", code="bad_rubric") from exc


def load_rubric(path: Path | str | None = None) -> Rubric:
    target = Path(path) if path else rubric_path()
    try:
        mtime = target.stat().st_mtime_ns
    except OSError as exc:
        raise InvalidInputError(f"Rubric file not found: {target}", code="bad_rubric") from exc
    return _load(str(target), mtime)


def validate_ratings(rubric: Rubric, ratings: dict[str, int]) -> None:
    expected = {c.key for c in rubric.criteria}
    missing, unknown = sorted(expected - ratings.keys()), sorted(ratings.keys() - expected)
    problems = []
    if missing:
        problems.append(f"missing criteria {missing}")
    if unknown:
        problems.append(f"unknown criteria {unknown}")
    out_of_range = sorted(k for k, v in ratings.items() if not rubric.scale.min <= v <= rubric.scale.max)
    if out_of_range:
        problems.append(f"ratings outside {rubric.scale.min}-{rubric.scale.max}: {out_of_range}")
    if problems:
        raise InvalidInputError("; ".join(problems), code="invalid_ratings")


def compute_priority(rubric: Rubric, ratings: dict[str, int]) -> Priority:
    """Weighted average of the ratings, rescaled to 0-100. Needs a rating for every criterion."""
    validate_ratings(rubric, ratings)
    span = rubric.scale.max - rubric.scale.min
    total_weight = sum(c.weight for c in rubric.criteria)
    lines = []
    for criterion in rubric.criteria:
        rating = ratings[criterion.key]
        normalized = (rating - rubric.scale.min) / span
        lines.append(
            PriorityLine(
                key=criterion.key,
                rating=rating,
                weight=criterion.weight,
                normalized=round(normalized, 4),
                points=round(100 * criterion.weight * normalized / total_weight, 2),
            )
        )
    total = 100 * sum(c.weight * (ratings[c.key] - rubric.scale.min) / span for c in rubric.criteria) / total_weight
    return Priority(rubric_name=rubric.name, rubric_version=rubric.version, total=round(total, 2), lines=lines)


class ScoringService:
    def __init__(self, session: Session, rubric: Rubric | None = None):
        self.session = session
        self.rubric = rubric or load_rubric()
        self.cards = CardRepository(session)
        self.scores = ScoreRepository(session)
        self.drafts = DraftRepository(session)

    def save_scores(
        self, card_id: int, scores: list[CriterionScore], run_id: int | None = None
    ) -> tuple[Score, Priority]:
        """Store a set of ratings as a Score row. The card's own priority_score is not touched."""
        if self.cards.get(card_id) is None:
            raise NotFoundError(f"Card {card_id} not found")
        if run_id is not None and not self.drafts.run_exists(run_id):
            raise NotFoundError(f"AI run {run_id} not found")
        keys = [s.key for s in scores]
        if len(set(keys)) != len(keys):
            raise InvalidInputError("Each criterion may be scored once", code="invalid_ratings")
        ratings = {s.key: s.rating for s in scores}
        priority = compute_priority(self.rubric, ratings)
        weights = {c.key: c.weight for c in self.rubric.criteria}
        row = self.scores.add(
            Score(
                card_id=card_id,
                run_id=run_id,
                rubric_name=self.rubric.name,
                rubric_version=self.rubric.version,
                per_criterion={
                    s.key: {"rating": s.rating, "rationale": s.rationale, "weight": weights[s.key]} for s in scores
                },
                total=priority.total,
            )
        )
        return row, priority

    def priority_for_card(self, card_id: int) -> Priority:
        """Priority from the card's most recent saved scores."""
        if self.cards.get(card_id) is None:
            raise NotFoundError(f"Card {card_id} not found")
        latest = self.scores.latest(card_id)
        if latest is None:
            raise NotFoundError(f"Card {card_id} has no saved scores", code="no_scores")
        return compute_priority(self.rubric, {k: int(v["rating"]) for k, v in latest.per_criterion.items()})
