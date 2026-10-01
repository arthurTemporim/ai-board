"""Pure scoring of find_related predictions against labelled pairs. No I/O, so it is easy to test."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import combinations

TRUTH = ("duplicate", "related", "distinct")
# What the workflow did with a pair: linked as duplicate / related, looked at but not linked, or never looked at.
PREDICTED = ("duplicate", "related", "none", "not_retrieved")


@dataclass(frozen=True)
class Judgement:
    """The best judgement the workflow made for one pair of cards (the more confident of the two directions)."""

    verdict: str  # duplicate | related | distinct
    confidence: float
    similarity: float


@dataclass
class ClassScores:
    true_positives: int = 0
    predicted: int = 0
    actual: int = 0

    @property
    def precision(self) -> float | None:
        return self.true_positives / self.predicted if self.predicted else None

    @property
    def recall(self) -> float | None:
        return self.true_positives / self.actual if self.actual else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)


@dataclass
class EvalScores:
    confusion: dict[str, dict[str, int]]
    duplicate: ClassScores
    related: ClassScores
    merge_grade: ClassScores  # duplicates confident enough to be proposed for merging
    retrieval_recall: dict[str, float | None]  # share of labelled pairs the similarity search surfaced at all
    false_merges: list[tuple[int, int]] = field(default_factory=list)  # confident duplicates that are not duplicates
    misses: list[tuple[int, int, str, str]] = field(default_factory=list)  # (a, b, truth, what happened)
    false_links: list[tuple[int, int, str, str]] = field(default_factory=list)  # (a, b, truth, predicted)
    pairs: int = 0


def pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def truth_labels(labels: dict, board_of: dict[int, int]) -> dict[tuple[int, int], str]:
    """Truth for every pair of cards on the same board: labelled duplicates and related pairs, the rest distinct."""
    truth = {pair(a, b): "distinct" for a, b in combinations(sorted(board_of), 2) if board_of[a] == board_of[b]}
    for a, b in labels["related_pairs"]:
        truth[pair(a, b)] = "related"
    for a, b in labels["duplicate_pairs"]:
        truth[pair(a, b)] = "duplicate"  # a duplicate pair wins if a pair appears in both lists
    return truth


def predicted_label(j: Judgement | None, link_threshold: float) -> str:
    if j is None:
        return "not_retrieved"
    if j.verdict in ("duplicate", "related") and j.confidence >= link_threshold:
        return j.verdict
    return "none"


def score(
    truth: dict[tuple[int, int], str],
    judgements: dict[tuple[int, int], Judgement],
    *,
    link_threshold: float,
    merge_threshold: float,
) -> EvalScores:
    confusion = {t: dict.fromkeys(PREDICTED, 0) for t in TRUTH}
    duplicate, related, merge_grade = ClassScores(), ClassScores(), ClassScores()
    scores = EvalScores(confusion, duplicate, related, merge_grade, {}, pairs=len(truth))
    retrieved = {"duplicate": [0, 0], "related": [0, 0]}  # [retrieved, total]

    for key, actual in sorted(truth.items()):
        j = judgements.get(key)
        predicted = predicted_label(j, link_threshold)
        confusion[actual][predicted] += 1
        if actual in retrieved:
            retrieved[actual][1] += 1
            retrieved[actual][0] += j is not None
        is_merge = predicted == "duplicate" and j is not None and j.confidence >= merge_threshold

        if actual == "duplicate":
            duplicate.actual += 1
            merge_grade.actual += 1
        if actual == "related":
            related.actual += 1
        if predicted == "duplicate":
            duplicate.predicted += 1
            duplicate.true_positives += actual == "duplicate"
        if predicted == "related":
            related.predicted += 1
            related.true_positives += actual == "related"
        if is_merge:
            merge_grade.predicted += 1
            merge_grade.true_positives += actual == "duplicate"
            if actual != "duplicate":
                scores.false_merges.append(key)

        if actual != "distinct" and predicted != actual:
            what = "not retrieved by similarity search" if j is None else f"judged {j.verdict} at {j.confidence:.2f}"
            scores.misses.append((*key, actual, what))
        if predicted in ("duplicate", "related") and predicted != actual:
            scores.false_links.append((*key, actual, predicted))

    scores.retrieval_recall = {k: (v[0] / v[1] if v[1] else None) for k, v in retrieved.items()}
    return scores


def best_judgements(entries: Iterable[tuple[tuple[int, int], Judgement]]) -> dict[tuple[int, int], Judgement]:
    """Keep the most confident judgement per pair; a pair looked at from both cards is judged twice."""
    best: dict[tuple[int, int], Judgement] = {}
    for key, judgement in entries:
        if key not in best or judgement.confidence > best[key].confidence:
            best[key] = judgement
    return best
