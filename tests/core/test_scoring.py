from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.errors import InvalidInputError
from core.services.scoring import Criterion, Rubric, Scale, compute_priority, load_rubric, validate_ratings


def rubric(**kwargs) -> Rubric:
    data = {
        "name": "t",
        "version": 1,
        "scale": Scale(min=1, max=5),
        "criteria": [Criterion(key="a", label="A", weight=1), Criterion(key="b", label="B", weight=3)],
    }
    return Rubric(**{**data, **kwargs})


def test_priority_is_the_weighted_average_rescaled_to_100():
    result = compute_priority(rubric(), {"a": 5, "b": 1})
    assert result.total == 25.0  # only the weight-1 criterion is maxed: 1/4 of the points
    assert [(line.key, line.points) for line in result.lines] == [("a", 25.0), ("b", 0.0)]
    assert compute_priority(rubric(), {"a": 3, "b": 3}).total == 50.0


def test_weights_scale_does_not_depend_on_their_absolute_size():
    heavy = rubric(criteria=[Criterion(key="a", label="A", weight=10), Criterion(key="b", label="B", weight=30)])
    assert compute_priority(heavy, {"a": 5, "b": 1}).total == compute_priority(rubric(), {"a": 5, "b": 1}).total


def test_a_different_scale_is_normalised():
    r = rubric(scale=Scale(min=0, max=10))
    assert compute_priority(r, {"a": 10, "b": 10}).total == 100.0
    assert compute_priority(r, {"a": 0, "b": 0}).total == 0.0
    assert compute_priority(r, {"a": 5, "b": 5}).total == 50.0


@pytest.mark.parametrize(
    "ratings, message",
    [({"a": 1}, "missing"), ({"a": 1, "b": 1, "c": 1}, "unknown"), ({"a": 9, "b": 1}, "outside 1-5")],
)
def test_ratings_must_cover_the_rubric_exactly(ratings, message):
    with pytest.raises(InvalidInputError, match=message):
        validate_ratings(rubric(), ratings)


@pytest.mark.parametrize(
    "change",
    [
        {"criteria": [Criterion(key="a", label="A", weight=1), Criterion(key="a", label="A2", weight=1)]},
        {"criteria": []},
        {"scale": {"min": 5, "max": 5}},
        {"criteria": [Criterion(key="a", label="A", weight=1, anchors={9: "off the scale"})]},
        {"version": 0},
    ],
)
def test_invalid_rubrics_are_rejected(change):
    with pytest.raises(ValidationError):
        rubric(**change)


def test_criterion_weight_must_be_positive_and_key_a_slug():
    for bad in ({"weight": 0}, {"weight": -1}, {"key": "Not A Slug"}):
        with pytest.raises(ValidationError):
            Criterion(**{"key": "a", "label": "A", "weight": 1, **bad})


def test_the_shipped_rubric_is_valid_and_ratings_always_help_priority():
    shipped = load_rubric()
    keys = [c.key for c in shipped.criteria]
    low = compute_priority(shipped, {k: 1 for k in keys}).total
    high = compute_priority(shipped, {k: 5 for k in keys}).total
    assert (low, high) == (0.0, 100.0)
    for key in keys:  # raising any single rating never lowers the priority
        raised = {k: 1 for k in keys} | {key: 5}
        assert compute_priority(shipped, raised).total > low
