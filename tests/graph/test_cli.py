"""`python -m ai.graph.run`: argument handling, printed results, and pause/resume across two invocations."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from ai.graph.run import parse_args
from tests.graph.conftest import DISTINCT, DUPLICATE_OF_SAML, VAGUE, add_card


# ---- argument parsing (in process) ------------------------------------------------------------
def parse(*argv: str):
    return parse_args(list(argv))


def test_steps_are_normalised_to_canonical_names():
    args = parse("--card-id", "3", "--steps", "score,analyze")
    assert args.card_id == 3 and args.steps_list == ["analyze_need", "score"]
    assert parse("--card-id", "3").steps_list is None


@pytest.mark.parametrize(
    "argv, message",
    [
        ([], "give --card-id"),
        (["--card-id", "x"], "invalid int value"),
        (["--card-id", "3", "--steps", "fly"], "Unknown step 'fly'"),
        (["--resume", "4"], "--resume needs --decision"),
        (["--resume", "4", "--decision", "{nope"], "not valid JSON"),
        (["--resume", "4", "--decision", "{}", "--card-id", "3"], "do not combine"),
        (["--resume", "4", "--decision", "{}", "--steps", "score"], "do not combine"),
    ],
)
def test_bad_arguments_exit_with_a_usage_error(argv, message, capsys):
    with pytest.raises(SystemExit) as exit_info:
        parse_args(argv)
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


def test_resume_arguments_are_parsed():
    args = parse("--resume", "12", "--decision", '{"verdict": "duplicate"}')
    assert args.resume == 12 and args.decision_value == {"verdict": "duplicate"}


# ---- running it (subprocess) ------------------------------------------------------------------
@pytest.fixture
def cli(db, tmp_path):
    env = {**os.environ, **db.env, "MOCK_AI": "1", "GRAPH_CHECKPOINT_PATH": str(tmp_path / "cli-checkpoints.db")}
    for name in ("GRAPH_MIN_SIMILARITY", "GRAPH_MERGE_THRESHOLD", "GRAPH_LINK_THRESHOLD"):
        env.pop(name, None)
    env["GRAPH_MIN_SIMILARITY"] = "0.05"

    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "ai.graph.run", *argv], capture_output=True, text=True, timeout=180, env=env
        )

    return run


def test_steps_analyze_and_score_print_their_results(db, cli):
    card_id = add_card(db, *DISTINCT)
    done = cli("--card-id", str(card_id), "--steps", "analyze,score")

    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert f"on card {card_id}: succeeded, outcome completed" in out
    lines = {line.split()[0]: line for line in out.splitlines() if line.startswith("  ")}
    assert "succeeded" in lines["analyze_need"] and "actionable" in lines["analyze_need"]
    assert "succeeded" in lines["score"] and "priority" in lines["score"] and "/100" in lines["score"]
    for skipped in (
        "validate_intake",
        "find_related",
        "group_problem",
        "suggest_tags",
        "decision_brief",
        "draft_comms",
    ):
        assert "skipped" in lines[skipped]
    assert "2 suggestion(s) waiting for a person: 1 analysis, 1 score" in out


def test_a_full_run_lists_every_step(db, cli):
    card_id = add_card(db, *DISTINCT)
    done = cli("--card-id", str(card_id))
    assert done.returncode == 0, done.stderr
    for step in (
        "validate_intake",
        "analyze_need",
        "find_related",
        "group_problem",
        "score",
        "suggest_tags",
        "decision_brief",
        "draft_comms",
    ):
        assert any(line.split()[:2] == [step, "succeeded"] for line in done.stdout.splitlines() if line.strip()), step


def test_json_output_is_the_whole_report(db, cli):
    card_id = add_card(db, *DISTINCT)
    done = cli("--card-id", str(card_id), "--steps", "score", "--json")
    report = json.loads(done.stdout)
    assert report["status"] == "succeeded" and report["card_id"] == card_id
    assert report["steps"]["score"] == "succeeded" and "ratings" in report["results"]["score"]
    assert [s["kind"] for s in report["suggestions"]] == ["score"]


def test_the_vague_route_is_visible_in_the_output(db, cli):
    card_id = add_card(db, *VAGUE)
    done = cli("--card-id", str(card_id))
    assert "outcome needs_info" in done.stdout and "NOT actionable" in done.stdout
    assert any(line.split()[:2] == ["needs_info", "succeeded"] for line in done.stdout.splitlines() if line.strip())


def test_a_paused_run_can_be_resumed_by_a_second_invocation(db, cli):
    card_id = add_card(db, *DUPLICATE_OF_SAML)
    paused = cli("--card-id", str(card_id), "--merge-threshold", "0.95")

    assert paused.returncode == 0, paused.stderr
    assert "awaiting_human" in paused.stdout and "Paused: a person has to answer" in paused.stdout
    assert '"kind": "confirm_duplicate"' in paused.stdout
    resume_line = next(line for line in paused.stdout.splitlines() if "--resume" in line)
    run_id = resume_line.split("--resume ")[1].split()[0]

    done = cli("--resume", run_id, "--decision", '{"verdict": "duplicate"}')
    assert done.returncode == 0, done.stderr
    assert "succeeded, outcome duplicate_proposed" in done.stdout
    assert "merge #" in done.stdout and "human_checkpoint" in done.stdout


def test_resuming_a_run_that_is_not_waiting_is_a_clear_error(db, cli):
    card_id = add_card(db, *DISTINCT)
    finished = json.loads(cli("--card-id", str(card_id), "--steps", "score", "--json").stdout)
    done = cli("--resume", str(finished["run_id"]), "--decision", '{"actionable": true}')
    assert done.returncode == 2 and "not waiting for an answer" in done.stderr
    assert cli("--resume", "424242", "--decision", "{}").returncode == 2


def test_a_missing_card_exits_with_failure(db, cli):
    done = cli("--card-id", "424242")
    assert done.returncode == 1
    assert "failed" in done.stdout and "Card 424242 not found" in done.stdout


def test_bad_settings_are_reported_before_anything_starts(db, cli):
    done = cli("--card-id", "1", "--link-threshold", "0.9", "--merge-threshold", "0.5")
    assert done.returncode == 2 and "cannot be higher" in done.stderr
    assert cli("--card-id", "1", "--steps", "fly").returncode == 2
