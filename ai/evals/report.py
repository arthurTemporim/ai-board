"""Markdown for docs/EVAL_RESULTS.md."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ai.evals.scoring import PREDICTED, TRUTH, ClassScores

if TYPE_CHECKING:
    from ai.evals.run import ModeResult


def _f(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _class_row(name: str, c: ClassScores) -> str:
    return f"| {name} | {_f(c.precision)} | {_f(c.recall)} | {_f(c.f1)} | {c.true_positives} | {c.predicted} | {c.actual} |"


def _title(titles: dict[int, str], card_id: int) -> str:
    return f"#{card_id} {titles.get(card_id, '?')}"


def render_mode(r: ModeResult) -> str:
    s, c = r.scores, r.config
    out = [
        f"## {r.mode.capitalize()} mode",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Judge | `{r.llm_model}` with prompt `{r.prompt}` |",
        f"| Embeddings | `{r.embedder}` |",
        f"| Candidates per card (`top_k`) | {c['top_k']} (similarity at least {c['min_similarity']}) |",
        f"| Link threshold / merge threshold | {c['link_threshold']} / {c['merge_threshold']} |",
        f"| Cards run | {r.cards_run}; {r.candidates_judged} pairs judged; {s.pairs} labelled pairs scored |",
        f"| Time | {r.seconds:.0f} s |",
        "",
        "| Class | Precision | Recall | F1 | Correct | Predicted | Actual |",
        "|---|---|---|---|---|---|---|",
        _class_row("duplicate (linked)", s.duplicate),
        _class_row("duplicate, merge-grade", s.merge_grade),
        _class_row("related (linked)", s.related),
        "",
        f"Retrieval recall (labelled pairs the similarity search surfaced at all, before any judging): "
        f"duplicates **{_f(s.retrieval_recall.get('duplicate'))}**, related **{_f(s.retrieval_recall.get('related'))}**. "
        f"False merges (a confident duplicate verdict on a pair that is not a duplicate): **{len(s.false_merges)}**.",
        "",
        "Confusion matrix (rows: truth, columns: what the workflow did with the pair):",
        "",
        "| truth \\ workflow | " + " | ".join(PREDICTED) + " |",
        "|---|" + "---|" * len(PREDICTED),
    ]
    for truth in TRUTH:
        out.append(f"| **{truth}** | " + " | ".join(str(s.confusion[truth][p]) for p in PREDICTED) + " |")
    out += [
        "",
        "`none` = looked at but not linked (judged distinct, or below the link threshold); `not_retrieved` = never looked at.",
        "",
    ]

    if r.sweep:
        out += [
            "Sensitivity to the link threshold (the same judgements scored at other thresholds; the merge threshold stays "
            "at least as high):",
            "",
            "| Link threshold | Duplicate precision | Duplicate recall | Related precision | Related recall | Links suggested | Wrong links |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in r.sweep:
            out.append(
                f"| {row['link_threshold']} | {_f(row['duplicate_precision'])} | {_f(row['duplicate_recall'])} | "
                f"{_f(row['related_precision'])} | {_f(row['related_recall'])} | {row['links']} | {row['wrong_links']} |"
            )
        out += [
            "",
            "Choosing a threshold from this table fits it to these same 10 labelled pairs, so the best-looking row is "
            "optimistic. Use it to see the trade-off, then confirm on data the model has not been tuned against.",
            "",
        ]
    if s.misses:
        out += ["Labelled pairs the workflow got wrong or missed:", ""]
        out += [
            f"- **{truth}** {_title(r.titles, a)} / {_title(r.titles, b)}: {what}" for a, b, truth, what in s.misses
        ]
        out.append("")
    if s.false_links:
        out += ["Links it would suggest that are wrong:", ""]
        out += [
            f"- predicted **{pred}**, truth **{truth}**: {_title(r.titles, a)} / {_title(r.titles, b)}"
            for a, b, truth, pred in s.false_links
        ]
        out.append("")
    if r.errors:
        out += [f"Errors during the run ({len(r.errors)}):", ""] + [f"- {e}" for e in r.errors[:10]] + [""]
    return "\n".join(out)


def render_markdown(
    results: list[ModeResult], *, generated: datetime, skipped_real: bool, subset: list[int] | None
) -> str:
    head = [
        "# Evaluation results: finding duplicates and related requests",
        "",
        f"Generated {generated:%Y-%m-%d %H:%M} UTC by `make eval` (`python -m ai.evals.run`). Regenerate it after changing a prompt, "
        "model, embedding model or threshold.",
        "",
        "## What this measures",
        "",
        "The `find_related` step of the triage workflow, run on every card of the seeded data in a throwaway database: "
        "the similarity search proposes candidates, the `judge_duplicate` prompt labels each as duplicate / related / distinct "
        "with a confidence, and pairs at or above the link threshold would become suggested links. That is compared with "
        "`data/seed/labels.json`: 5 groups of true duplicates (7 duplicate pairs), 3 related-but-distinct pairs, and every "
        "other pair of cards on the same board counted as distinct.",
        "",
        "**How far to trust it.** There are only 7 duplicate and 3 related pairs, so one pair is 14% of duplicate recall; "
        "treat differences of a pair or two as noise. The seed requests were written by hand for this purpose. A single run "
        "of a language model at temperature 0 is close to, but not exactly, repeatable. These numbers say whether the "
        "workflow is wired up and roughly how good it is; they do not predict accuracy on real customer data.",
        "",
    ]
    if subset:
        head += [f"*Partial run: only cards {', '.join(map(str, subset))}.*", ""]
    body = [render_mode(r) for r in results]
    tail: list[str] = []
    if skipped_real:
        tail += [
            "## Real mode",
            "",
            "Not run: no model is configured (`LITELLM_BASE_URL` or `ANTHROPIC_API_KEY`). Set one and run `make eval` again.",
            "",
        ]
    modes = {r.mode: r for r in results}
    if "mock" in modes:
        tail += [
            "## Reading the mock numbers",
            "",
            "Mock mode replaces the model with deterministic rules (word overlap) and the embeddings with a hashed bag of words. "
            "It exists so the whole system runs with no model, and this table shows what that costs: it finds duplicates only "
            "when they share words. It is a wiring test, not a quality measure.",
            "",
        ]
    return "\n".join([*head, *body, *tail]).rstrip() + "\n"
