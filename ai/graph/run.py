"""Run the triage workflow from the command line.

    python -m ai.graph.run --card-id 3 --steps analyze,score
    python -m ai.graph.run --card-id 3                      # every step
    python -m ai.graph.run --resume 12 --decision '{"actionable": true}'

Steps (aliases in brackets): validate_intake [intake], analyze_need [analyze], find_related [related],
group_problem [group], score, suggest_tags [tags], decision_brief [brief], draft_comms [comms].
MOCK_AI=1 (or --mock) runs everything with no model and no API key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from typing import Any

from ai.graph.state import STEPS, GraphConfig, normalise_steps
from ai.graph.workflow import RunReport, Workflow


def describe(step: str, result: dict[str, Any]) -> str:
    """One line about what a step produced."""
    match step:
        case "validate_intake":
            warnings = "; ".join(result["warnings"]) or "no warnings"
            return f"valid ({warnings})"
        case "analyze_need":
            verdict = "actionable" if result["is_actionable"] else "NOT actionable (needs more detail)"
            return f"{verdict}, confidence {result['confidence']:.2f}: {result['underlying_need']}"
        case "find_related":
            judged = [c for c in result["candidates"] if c.get("verdict")]
            parts = [
                f"{len(result['candidates'])} similar cards, {len(judged)} judged, "
                f"{result['links_created']} link(s) suggested"
            ]
            for c in judged:
                parts.append(
                    f"    #{c['card_id']} {c['title'][:50]!r}: {c['verdict']} {c['confidence']:.2f} -> {c['action']}"
                )
            return "\n".join(parts)
        case "group_problem":
            return f"{result['decision']} ({result['applied']}), confidence {result['confidence']:.2f}"
        case "score":
            ratings = ", ".join(f"{k}={v}" for k, v in result["ratings"].items())
            return f"priority {result['total']:.1f}/100 ({ratings})"
        case "suggest_tags":
            names = ", ".join(t["name"] + (" (skipped)" if "skipped" in t else "") for t in result["tags"])
            return names or "no tags suggested"
        case "decision_brief":
            return f"{result['recommendation']}: {result['summary']}"
        case "draft_comms":
            return "; ".join(f"{d['audience']}: {d['subject']}" for d in result["drafts"])
        case "human_checkpoint":
            return f"{result['kind']} -> {json.dumps(result['decision'])}"
        case "needs_info":
            return f"asked {len(result['questions'])} question(s)"
        case "merge_proposal":
            return (
                f"merge #{result['merge_card_id']} into #{result['keep_card_id']} "
                f"(confidence {result['confidence']:.2f})"
            )
    return json.dumps(result)[:200]


def print_report(report: RunReport) -> None:
    print(
        f"Run {report.run_id} on card {report.card_id}: {report.status}"
        + (f", outcome {report.outcome}" if report.outcome else "")
    )
    print()
    routes = [s for s in ("human_checkpoint", "needs_info", "merge_proposal") if s in report.results]
    for step in [*STEPS, *routes]:
        status = report.steps.get(step)
        if status is None and step not in report.results:
            continue
        line = describe(step, report.results[step]) if step in report.results else ""
        print(f"  {step:<17} {status or 'succeeded':<10} {line}")
    if report.errors:
        print("\nErrors:")
        for error in report.errors:
            print(f"  {error['step']}: {error['error']}")
    if report.suggestions:
        kinds = Counter(s["kind"] for s in report.suggestions)
        print(
            f"\n{len(report.suggestions)} suggestion(s) waiting for a person: "
            + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        )
    if report.interrupt:
        print("\nPaused: a person has to answer this before the run can continue.")
        print(json.dumps(report.interrupt, indent=2))
        print(f"\nResume with:\n  python -m ai.graph.run --resume {report.run_id} --decision '<json>'")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ai.graph.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--card-id", type=int, help="card to triage")
    parser.add_argument("--steps", help="comma-separated steps to run (default: all)")
    parser.add_argument("--resume", type=int, metavar="RUN_ID", help="continue a paused run")
    parser.add_argument("--decision", help="JSON answer for the paused run (with --resume)")
    parser.add_argument("--mock", action="store_true", help="use the mock model and embedder (same as MOCK_AI=1)")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--checkpoint-path", help="SQLite file for pause/resume state (env GRAPH_CHECKPOINT_PATH)")
    tuning = parser.add_argument_group("thresholds (defaults and GRAPH_* environment variables in ai/graph/state.py)")
    tuning.add_argument("--top-k", type=int)
    tuning.add_argument("--min-similarity", type=float)
    tuning.add_argument("--link-threshold", type=float)
    tuning.add_argument("--merge-threshold", type=float)
    tuning.add_argument("--analysis-min-confidence", type=float)
    tuning.add_argument("--audiences", help="comma-separated: requester,internal,sales")
    args = parser.parse_args(argv)

    if args.resume is None and args.card_id is None:
        parser.error("give --card-id to start a run, or --resume RUN_ID to continue one")
    if args.resume is not None and args.decision is None:
        parser.error("--resume needs --decision '<json>'")
    if args.resume is not None and (args.card_id is not None or args.steps):
        parser.error("--resume continues an existing run; do not combine it with --card-id or --steps")
    try:
        args.steps_list = normalise_steps(args.steps)
    except ValueError as exc:
        parser.error(str(exc))
    if args.decision is not None:
        try:
            args.decision_value = json.loads(args.decision)
        except json.JSONDecodeError as exc:
            parser.error(f"--decision is not valid JSON: {exc}")
    return args


async def main_async(args: argparse.Namespace) -> int:
    audiences = tuple(a.strip() for a in args.audiences.split(",")) if args.audiences else None
    try:
        config = GraphConfig.from_env(
            top_k=args.top_k,
            min_similarity=args.min_similarity,
            link_threshold=args.link_threshold,
            merge_threshold=args.merge_threshold,
            analysis_min_confidence=args.analysis_min_confidence,
            comms_audiences=audiences,
        )
    except ValueError as exc:
        print(f"Invalid settings: {exc}", file=sys.stderr)
        return 2

    async with Workflow(config=config, checkpoint_path=args.checkpoint_path) as workflow:
        try:
            if args.resume is not None:
                report = await workflow.resume(args.resume, args.decision_value)
            else:
                report = await workflow.start(args.card_id, args.steps_list)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print_report(report)
    return 1 if report.status == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mock:
        os.environ["MOCK_AI"] = "1"
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
