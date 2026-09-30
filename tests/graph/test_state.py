from __future__ import annotations

import pytest

from ai.graph.nodes import classify, parse_decision
from ai.graph.state import STEPS, GraphConfig, concat, merge_dicts, normalise_steps


def test_steps_are_in_graph_order_and_cover_every_task():
    assert STEPS == (
        "validate_intake", "analyze_need", "find_related", "group_problem",
        "score", "suggest_tags", "decision_brief", "draft_comms",
    )  # fmt: skip


@pytest.mark.parametrize(
    "given, expected",
    [
        (None, None),
        ("", None),
        ("all", None),
        ([], None),
        ("analyze,score", ["analyze_need", "score"]),
        ("score, analyze", ["analyze_need", "score"]),  # always in graph order
        (["brief", "comms", "tags"], ["suggest_tags", "decision_brief", "draft_comms"]),
        ("analyze_need,analyze", ["analyze_need"]),  # names and aliases, without repeats
        (" Score ", ["score"]),
        ("intake,related,group", ["validate_intake", "find_related", "group_problem"]),
    ],
)
def test_normalise_steps(given, expected):
    assert normalise_steps(given) == expected


def test_unknown_steps_are_rejected_with_the_known_names():
    with pytest.raises(ValueError, match="Unknown step 'fly'.*analyze_need"):
        normalise_steps("score,fly")
    with pytest.raises(ValueError, match="Unknown step 'human_checkpoint'"):
        normalise_steps("human_checkpoint")  # a route, not something a caller can request


def test_reducers_merge_instead_of_replacing():
    assert merge_dicts({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}
    assert merge_dicts(None, {"a": 1}) == {"a": 1} and merge_dicts({"a": 1}, None) == {"a": 1}
    assert concat([1], [2, 3]) == [1, 2, 3] and concat(None, [1]) == [1]


def test_config_defaults_are_consistent():
    config = GraphConfig()
    assert config.link_threshold < config.merge_threshold
    assert config.comms_audiences == ("requester",)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"link_threshold": 1.5}, "link_threshold must be between 0 and 1"),
        ({"merge_threshold": -0.1}, "merge_threshold"),
        ({"link_threshold": 0.9, "merge_threshold": 0.5}, "cannot be higher"),
        ({"top_k": 0}, "top_k"),
        ({"top_k": 99}, "top_k"),
        ({"comms_audiences": ()}, "comms_audiences"),
        ({"comms_audiences": ("press",)}, "comms_audiences"),
    ],
)
def test_config_rejects_bad_values(change, message):
    with pytest.raises(ValueError, match=message):
        GraphConfig(**change)


def test_config_reads_environment_and_explicit_values_win(monkeypatch):
    monkeypatch.setenv("GRAPH_LINK_THRESHOLD", "0.7")
    monkeypatch.setenv("GRAPH_TOP_K", "3")
    monkeypatch.setenv("GRAPH_COMMS_AUDIENCES", "requester, sales")
    monkeypatch.setenv("GRAPH_MERGE_THRESHOLD", "")
    config = GraphConfig.from_env()
    assert (config.link_threshold, config.top_k, config.comms_audiences) == (0.7, 3, ("requester", "sales"))
    assert config.merge_threshold == GraphConfig().merge_threshold  # blank means default
    assert GraphConfig.from_env(link_threshold=0.65, top_k=None).link_threshold == 0.65
    assert GraphConfig.from_env(top_k=None).top_k == 3


@pytest.mark.parametrize(
    "verdict, confidence, expected",
    [
        ("duplicate", 0.95, "merge"),
        ("duplicate", 0.85, "merge"),  # the merge threshold is inclusive
        ("duplicate", 0.849, "review"),
        ("duplicate", 0.6, "review"),  # so is the link threshold
        ("duplicate", 0.599, "none"),
        ("related", 0.99, "link_related"),
        ("related", 0.6, "link_related"),
        ("related", 0.5, "none"),
        ("distinct", 0.99, "none"),
    ],
)
def test_classify_uses_both_thresholds(verdict, confidence, expected):
    assert classify(verdict, confidence, link_threshold=0.6, merge_threshold=0.85) == expected


def test_classify_follows_configured_thresholds():
    assert classify("duplicate", 0.9, 0.6, 0.95) == "review"
    assert classify("duplicate", 0.9, 0.6, 0.9) == "merge"
    assert classify("related", 0.7, 0.8, 0.9) == "none"


DUP_REQUEST = {"kind": "confirm_duplicate", "payload": {"candidates": [{"card_id": 4}, {"card_id": 9}]}}


@pytest.mark.parametrize(
    "request_, value, expected",
    [
        ({"kind": "confirm_actionable"}, {"actionable": True}, {"actionable": True}),
        ({"kind": "confirm_actionable"}, {"actionable": False}, {"actionable": False}),
        (
            DUP_REQUEST,
            {"verdict": "related"},
            {"decisions": [{"card_id": 4, "verdict": "related"}, {"card_id": 9, "verdict": "related"}]},
        ),
        (
            DUP_REQUEST,
            {"decisions": [{"card_id": 9, "verdict": "duplicate"}, {"card_id": 4, "verdict": "distinct"}]},
            {"decisions": [{"card_id": 4, "verdict": "distinct"}, {"card_id": 9, "verdict": "duplicate"}]},
        ),
    ],
)  # fmt: skip
def test_valid_human_decisions(request_, value, expected):
    assert parse_decision(request_, value) == expected


@pytest.mark.parametrize(
    "request_, value, message",
    [
        ({"kind": "confirm_actionable"}, "yes", "JSON object"),
        ({"kind": "confirm_actionable"}, {"actionable": "yes"}, "actionable"),
        ({"kind": "confirm_actionable"}, {}, "actionable"),
        (DUP_REQUEST, {"verdict": "maybe"}, "verdict"),
        (DUP_REQUEST, {"decisions": "all"}, "decisions"),
        (DUP_REQUEST, {"decisions": [{"card_id": 4, "verdict": "duplicate"}]}, "no decision for card"),
        (DUP_REQUEST, {"decisions": [{"card_id": 77, "verdict": "duplicate"}]}, "card_id"),
    ],
)
def test_invalid_human_decisions_say_what_is_wrong(request_, value, message):
    with pytest.raises(ValueError, match=message):
        parse_decision(request_, value)
