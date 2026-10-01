"""Evaluate find_related on the seed data against data/seed/labels.json.

    python -m ai.evals.run                  # mock mode, plus real mode if a model is configured
    python -m ai.evals.run --mode mock      # or real / both / auto (default)
    python -m ai.evals.run --output docs/EVAL_RESULTS.md

For each mode this seeds a throwaway database, runs the real workflow's find_related step on every card
(similarity search through the MCP server, then the duplicate judge) and compares the links it would
suggest with the labelled duplicate and related pairs. Real mode uses whatever LLM_PROVIDER /
LITELLM_BASE_URL / ANTHROPIC_API_KEY and embedding settings are in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai.evals.report import render_markdown
from ai.evals.scoring import EvalScores, Judgement, best_judgements, pair, score, truth_labels
from ai.graph.state import GraphConfig
from ai.graph.workflow import Workflow
from ai.llm.factory import get_llm
from ai.llm.registry import default_registry
from core.db import Base, make_engine, make_session_factory, session_scope
from core.models import Card
from data.seed import seed as seed_module

LABELS_PATH = seed_module.LABELS_PATH
DEFAULT_OUTPUT = Path("docs/EVAL_RESULTS.md")
SWEEP_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)


@dataclass
class ModeResult:
    mode: str
    scores: EvalScores
    config: dict[str, Any]
    llm_model: str
    prompt: str
    embedder: str
    cards_run: int
    candidates_judged: int
    errors: list[str]
    seconds: float
    titles: dict[int, str]
    sweep: list[dict[str, Any]]


def real_mode_available() -> bool:
    """A model is configured: a litellm proxy, or an Anthropic key."""
    return bool(os.environ.get("LITELLM_BASE_URL") or os.environ.get("ANTHROPIC_API_KEY"))


def collect(card_id: int, find_related_result: dict[str, Any]) -> list[tuple[tuple[int, int], Judgement]]:
    """Judgements made by one card's find_related run (verdicts above or below the link threshold alike)."""
    found = []
    for candidate in find_related_result.get("candidates", []):
        if candidate.get("verdict") is None:
            continue
        found.append(
            (
                pair(card_id, candidate["card_id"]),
                Judgement(candidate["verdict"], float(candidate["confidence"]), float(candidate["similarity"])),
            )
        )
    return found


async def evaluate(mode: str, config: GraphConfig, card_ids: list[int] | None = None) -> ModeResult:
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"eval-{mode}-") as tmp:
        url = f"sqlite:///{tmp}/eval.db"
        engine = make_engine(url)
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)
        with session_scope(factory) as session:
            seed_module.seed(session)
            cards = {c.id: c for c in session.query(Card).all()}
            board_of = {cid: c.board_id for cid, c in cards.items()}
            titles = {cid: c.title for cid, c in cards.items()}
        expected = {int(k): v["title"] for k, v in labels["cards"].items()}
        if titles != expected:
            raise RuntimeError(
                "The seeded cards do not match data/seed/labels.json; run `python -m data.seed.seed --write-labels`"
            )

        env = {"DATABASE_URL": url, "MOCK_AI": "1" if mode == "mock" else "0"}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)  # the LLM factory reads MOCK_AI from this process
        try:
            llm = get_llm()
            prompt = default_registry().load("judge_duplicate")
            ids = card_ids or sorted(cards)
            entries: list[tuple[tuple[int, int], Judgement]] = []
            errors: list[str] = []
            judged = 0
            embedder = ""
            async with Workflow(
                session_factory=factory,
                env=env,
                llm=llm,
                config=config,
                checkpoint_path=Path(tmp) / "cp.db",
                servers=["cards", "similarity"],
            ) as workflow:
                for card_id in ids:
                    report = await workflow.start(card_id, steps="related")
                    result = report.results.get("find_related")
                    if result is None:
                        errors.append(f"card {card_id}: find_related produced no result ({report.errors})")
                        continue
                    errors += [f"card {card_id}: {e['error']}" for e in report.errors]
                    entries += collect(card_id, result)
                    judged += sum(1 for c in result["candidates"] if c.get("verdict"))
                    if not embedder:
                        embedder = (await workflow.ctx.tool("find_similar_cards", card_id=card_id, top_k=1)).get(
                            "model", ""
                        )
            model = llm.model_for(
                default_registry().render(
                    "judge_duplicate",
                    {k: (0.5 if k == "similarity_score" else "x") for k in (v.name for v in prompt.variables)},
                )
            )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    truth = truth_labels(labels, board_of)
    # With a subset of cards, only judge the pairs those cards could have produced.
    if card_ids:
        truth = {k: v for k, v in truth.items() if k[0] in card_ids or k[1] in card_ids}
    best = best_judgements(entries)
    scores = score(truth, best, link_threshold=config.link_threshold, merge_threshold=config.merge_threshold)
    sweep = []
    for threshold in SWEEP_THRESHOLDS:  # the same judgements, scored at other link thresholds: no extra model calls
        s = score(truth, best, link_threshold=threshold, merge_threshold=max(threshold, config.merge_threshold))
        sweep.append(
            {
                "link_threshold": threshold,
                "duplicate_precision": s.duplicate.precision,
                "duplicate_recall": s.duplicate.recall,
                "related_precision": s.related.precision,
                "related_recall": s.related.recall,
                "links": s.duplicate.predicted + s.related.predicted,
                "wrong_links": len(s.false_links),
            }
        )
    return ModeResult(
        mode=mode,
        scores=scores,
        config=asdict(config),
        llm_model=model,
        prompt=prompt.ref,
        embedder=embedder,
        cards_run=len(ids),
        candidates_judged=judged,
        errors=errors,
        seconds=time.monotonic() - started,
        titles=titles,
        sweep=sweep,
    )


def print_summary(result: ModeResult) -> None:
    s = result.scores

    def fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.2f}"

    print(
        f"\n=== {result.mode} mode: {result.llm_model} + {result.embedder} "
        f"({result.seconds:.0f}s, {result.candidates_judged} judgements)"
    )
    for name, c in (("duplicate", s.duplicate), ("merge-grade duplicate", s.merge_grade), ("related", s.related)):
        print(
            f"  {name:<22} precision {fmt(c.precision)}  recall {fmt(c.recall)}  "
            f"({c.true_positives}/{c.predicted} predicted, {c.actual} actual)"
        )
    recall = s.retrieval_recall
    print(f"  retrieval recall: duplicates {fmt(recall['duplicate'])}, related {fmt(recall['related'])}")
    print(f"  false merges: {len(s.false_merges)}   errors: {len(result.errors)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ai.evals.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=["auto", "mock", "real", "both"], default="auto")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="markdown file to write (default docs/EVAL_RESULTS.md)"
    )
    parser.add_argument("--no-write", action="store_true", help="print only; do not write the results file")
    parser.add_argument("--cards", help="comma-separated card ids to run (default: all 30)")
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--min-similarity", type=float)
    parser.add_argument("--link-threshold", type=float)
    parser.add_argument("--merge-threshold", type=float)
    parser.add_argument(
        "--fail-below-recall",
        type=float,
        metavar="R",
        help="exit 1 if duplicate recall in any mode is below R (for CI)",
    )
    return parser.parse_args(argv)


def modes_to_run(choice: str) -> list[str]:
    if choice == "mock":
        return ["mock"]
    if choice == "real":
        return ["real"]
    if choice == "both":
        return ["mock", "real"]
    return ["mock", "real"] if real_mode_available() else ["mock"]


async def main_async(args: argparse.Namespace) -> int:
    overrides = {
        "top_k": args.top_k,
        "min_similarity": args.min_similarity,
        "link_threshold": args.link_threshold,
        "merge_threshold": args.merge_threshold,
    }
    try:
        config = GraphConfig.from_env(**overrides)
    except ValueError as exc:
        print(f"Invalid settings: {exc}", file=sys.stderr)
        return 2
    modes = modes_to_run(args.mode)
    if "real" in modes and not real_mode_available():
        print("Real mode needs LITELLM_BASE_URL or ANTHROPIC_API_KEY; neither is set.", file=sys.stderr)
        return 2
    card_ids = [int(x) for x in args.cards.split(",")] if args.cards else None

    results: list[ModeResult] = []
    skipped_real = args.mode == "auto" and "real" not in modes
    for mode in modes:
        print(f"Running {mode} mode ...", flush=True)
        result = await evaluate(mode, config, card_ids)
        print_summary(result)
        results.append(result)

    if not args.no_write:
        text = render_markdown(results, generated=datetime.now(UTC), skipped_real=skipped_real, subset=card_ids)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"\nWrote {args.output}")
    if args.fail_below_recall is not None:
        low = [r.mode for r in results if (r.scores.duplicate.recall or 0.0) < args.fail_below_recall]
        if low:
            print(f"Duplicate recall below {args.fail_below_recall} in: {', '.join(low)}", file=sys.stderr)
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
