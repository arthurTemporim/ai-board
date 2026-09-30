from __future__ import annotations

import pytest
from sqlalchemy import select

from core.models import Card, Score
from core.services.scoring import load_rubric
from tests.mcp.conftest import SCORING_MODULE, tools_for

pytestmark = pytest.mark.anyio

# Default rubric weights are 3, 2, 2, 1 (sum 8) on a 1-5 scale.
MIXED = {"customer_impact": 5, "demand_breadth": 3, "strategic_fit": 1, "effort_fit": 3}
MIXED_TOTAL = 100 * (3 * 1.0 + 2 * 0.5 + 2 * 0.0 + 1 * 0.5) / 8  # 56.25


def as_scores(ratings: dict[str, int]) -> list[dict]:
    return [{"key": k, "rating": v, "rationale": f"why {k}"} for k, v in ratings.items()]


async def test_get_rubric_returns_the_yaml_config(db):
    async with tools_for(SCORING_MODULE, db) as t:
        rubric = await t.ok("get_rubric")
    expected = load_rubric()
    assert rubric["name"] == expected.name and rubric["version"] == expected.version
    assert rubric["scale"] == {"min": 1, "max": 5}
    assert [c["key"] for c in rubric["criteria"]] == [
        "customer_impact",
        "demand_breadth",
        "strategic_fit",
        "effort_fit",
    ]
    assert [c["weight"] for c in rubric["criteria"]] == [3, 2, 2, 1]
    assert all(c["description"] and c["label"] for c in rubric["criteria"])
    assert rubric["criteria"][0]["anchors"]["5"]  # anchors are included for the scoring prompt


async def test_save_scores_stores_a_score_and_returns_the_priority(db):
    card_id = db.ids["saml"]
    async with tools_for(SCORING_MODULE, db) as t:
        result = await t.ok("save_scores", card_id=card_id, scores=as_scores(MIXED), run_id=db.ids["run"])
    assert result["total"] == MIXED_TOTAL and result["applied_to_card"] is False
    assert (result["rubric_name"], result["rubric_version"]) == ("default", 1)
    assert sum(line["points"] for line in result["lines"]) == pytest.approx(MIXED_TOTAL, abs=0.02)

    with db.session() as s:
        (score,) = s.scalars(select(Score).where(Score.card_id == card_id)).all()
        assert (score.total, score.run_id, score.rubric_name, score.rubric_version) == (
            MIXED_TOTAL,
            db.ids["run"],
            "default",
            1,
        )
        assert score.per_criterion["customer_impact"] == {"rating": 5, "rationale": "why customer_impact", "weight": 3}
        assert s.get(Card, card_id).priority_score is None  # the card itself is untouched


async def test_extremes_map_to_0_and_100(db):
    async with tools_for(SCORING_MODULE, db) as t:
        low = await t.ok("save_scores", card_id=db.ids["dark"], scores=as_scores({k: 1 for k in MIXED}))
        high = await t.ok("save_scores", card_id=db.ids["dark"], scores=as_scores({k: 5 for k in MIXED}))
    assert (low["total"], high["total"]) == (0.0, 100.0)


@pytest.mark.parametrize(
    "ratings, message",
    [
        ({"customer_impact": 3}, "missing criteria"),
        ({**MIXED, "charisma": 3}, "unknown criteria \\['charisma'\\]"),
        ({**MIXED, "effort_fit": 6}, "ratings outside 1-5: \\['effort_fit'\\]"),
        ({**MIXED, "effort_fit": 0}, "ratings outside 1-5"),
    ],
)
async def test_save_scores_rejects_bad_ratings(db, ratings, message):
    async with tools_for(SCORING_MODULE, db) as t:
        error = await t.error("save_scores", card_id=db.ids["saml"], scores=as_scores(ratings))
    import re

    assert re.search(message, error)
    with db.session() as s:
        assert s.scalars(select(Score)).all() == []  # nothing stored on failure


async def test_save_scores_rejects_repeats_unknown_cards_and_runs(db):
    async with tools_for(SCORING_MODULE, db) as t:
        repeated = as_scores(MIXED) + [{"key": "effort_fit", "rating": 1, "rationale": ""}]
        assert "may be scored once" in await t.error("save_scores", card_id=db.ids["saml"], scores=repeated)
        assert "Card 999 not found" in await t.error("save_scores", card_id=999, scores=as_scores(MIXED))
        assert "AI run 777 not found" in await t.error(
            "save_scores", card_id=db.ids["saml"], scores=as_scores(MIXED), run_id=777
        )
        assert "scores" in await t.error("save_scores", card_id=db.ids["saml"], scores=[])


async def test_compute_priority_from_ratings_is_pure(db):
    async with tools_for(SCORING_MODULE, db) as t:
        result = await t.ok("compute_priority", ratings=MIXED)
    assert result["total"] == MIXED_TOTAL
    by_key = {line["key"]: line for line in result["lines"]}
    assert by_key["customer_impact"] == {
        "key": "customer_impact",
        "rating": 5,
        "weight": 3.0,
        "normalized": 1.0,
        "points": 37.5,
    }
    assert by_key["strategic_fit"]["points"] == 0.0
    with db.session() as s:
        assert s.scalars(select(Score)).all() == []  # nothing stored


async def test_compute_priority_from_a_card_uses_its_latest_scores(db):
    card_id = db.ids["saml"]
    async with tools_for(SCORING_MODULE, db) as t:
        assert "no saved scores" in await t.error("compute_priority", card_id=card_id)
        await t.ok("save_scores", card_id=card_id, scores=as_scores({k: 1 for k in MIXED}))
        await t.ok("save_scores", card_id=card_id, scores=as_scores(MIXED))
        result = await t.ok("compute_priority", card_id=card_id)
    assert result["total"] == MIXED_TOTAL


async def test_compute_priority_needs_exactly_one_input(db):
    async with tools_for(SCORING_MODULE, db) as t:
        assert "exactly one of card_id or ratings" in await t.error("compute_priority")
        assert "exactly one of card_id or ratings" in await t.error(
            "compute_priority", card_id=db.ids["saml"], ratings=MIXED
        )
        assert "missing criteria" in await t.error("compute_priority", ratings={"customer_impact": 3})
        assert "Card 999 not found" in await t.error("compute_priority", card_id=999)


async def test_a_custom_rubric_file_changes_the_weights(db, tmp_path):
    path = tmp_path / "rubric.yaml"
    path.write_text(
        "name: tiny\nversion: 7\nscale: {min: 0, max: 10}\ncriteria:\n"
        "  - {key: value, label: Value, weight: 1, description: d}\n"
        "  - {key: cost, label: Cost, weight: 3, description: d}\n",
        encoding="utf-8",
    )
    db.env["RUBRIC_PATH"] = str(path)
    async with tools_for(SCORING_MODULE, db) as t:
        rubric = await t.ok("get_rubric")
        priority = await t.ok("compute_priority", ratings={"value": 10, "cost": 0})
        saved = await t.ok("save_scores", card_id=db.ids["saml"], scores=as_scores({"value": 5, "cost": 10}))
    assert (rubric["name"], rubric["version"], [c["key"] for c in rubric["criteria"]]) == ("tiny", 7, ["value", "cost"])
    assert priority["total"] == 25.0  # only the light criterion is maxed
    assert (saved["rubric_name"], saved["rubric_version"], saved["total"]) == ("tiny", 7, 87.5)


async def test_a_broken_rubric_file_gives_a_clear_error(db, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: bad\nversion: 1\nscale: {min: 5, max: 1}\ncriteria: []\n", encoding="utf-8")
    db.env["RUBRIC_PATH"] = str(path)
    async with tools_for(SCORING_MODULE, db) as t:
        message = await t.error("get_rubric")
    assert "Invalid rubric" in message and "scale.min must be lower" in message
    db.env["RUBRIC_PATH"] = str(tmp_path / "missing.yaml")
    async with tools_for(SCORING_MODULE, db) as t:
        assert "Rubric file not found" in await t.error("get_rubric")
