from __future__ import annotations

import json

import pytest

from ai.evals.scoring import Judgement, best_judgements, pair, predicted_label, score, truth_labels
from data.seed import seed as seed_module

LINK, MERGE = 0.6, 0.85


def j(verdict, confidence=0.9, similarity=0.5):
    return Judgement(verdict, confidence, similarity)


# Cards 1..4 on board 1, 5..6 on board 2. Truth: 1-2 duplicate, 3-4 related, everything else distinct.
BOARDS = {1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 2}
LABELS = {"duplicate_pairs": [[1, 2]], "related_pairs": [[3, 4]]}


def test_truth_covers_every_pair_on_the_same_board_only():
    truth = truth_labels(LABELS, BOARDS)
    assert len(truth) == 6 + 1  # C(4,2) on board 1 plus the one pair on board 2
    assert truth[(1, 2)] == "duplicate" and truth[(3, 4)] == "related" and truth[(5, 6)] == "distinct"
    assert (1, 5) not in truth  # different boards are never compared
    assert sum(1 for v in truth.values() if v == "distinct") == 5


def test_a_pair_in_both_lists_counts_as_a_duplicate():
    truth = truth_labels({"duplicate_pairs": [[1, 2]], "related_pairs": [[2, 1]]}, BOARDS)
    assert truth[(1, 2)] == "duplicate"


@pytest.mark.parametrize(
    "judgement, expected",
    [
        (None, "not_retrieved"),
        (j("duplicate", 0.9), "duplicate"),
        (j("duplicate", 0.6), "duplicate"),  # the link threshold is inclusive
        (j("duplicate", 0.59), "none"),
        (j("related", 0.7), "related"),
        (j("distinct", 0.99), "none"),
    ],
)
def test_predicted_label(judgement, expected):
    assert predicted_label(judgement, LINK) == expected


def run(judgements):
    return score(truth_labels(LABELS, BOARDS), judgements, link_threshold=LINK, merge_threshold=MERGE)


def test_a_perfect_run():
    s = run({(1, 2): j("duplicate", 0.9), (3, 4): j("related", 0.8), (1, 3): j("distinct", 0.9)})
    assert (s.duplicate.precision, s.duplicate.recall, s.duplicate.f1) == (1.0, 1.0, 1.0)
    assert (s.related.precision, s.related.recall) == (1.0, 1.0)
    assert (s.merge_grade.precision, s.merge_grade.recall) == (1.0, 1.0)
    assert s.false_merges == [] and s.misses == [] and s.false_links == []
    assert s.retrieval_recall == {"duplicate": 1.0, "related": 1.0}
    assert s.confusion["duplicate"] == {"duplicate": 1, "related": 0, "none": 0, "not_retrieved": 0}
    assert s.confusion["distinct"] == {"duplicate": 0, "related": 0, "none": 1, "not_retrieved": 4}


def test_missing_a_duplicate_hurts_recall_and_says_why():
    s = run({(3, 4): j("related", 0.8)})
    assert s.duplicate.recall == 0.0 and s.duplicate.precision is None and s.duplicate.f1 is None
    assert s.retrieval_recall["duplicate"] == 0.0 and s.retrieval_recall["related"] == 1.0
    assert s.misses == [(1, 2, "duplicate", "not retrieved by similarity search")]


def test_a_retrieved_but_misjudged_duplicate_is_a_miss_of_a_different_kind():
    s = run({(1, 2): j("related", 0.9), (3, 4): j("related", 0.8)})
    assert s.retrieval_recall["duplicate"] == 1.0 and s.duplicate.recall == 0.0
    assert s.misses == [(1, 2, "duplicate", "judged related at 0.90")]
    assert s.false_links == [(1, 2, "duplicate", "related")]  # mislabelled, so wrong as a link


def test_false_links_and_false_merges():
    s = run(
        {
            (1, 2): j("duplicate", 0.9),
            (1, 3): j("duplicate", 0.95),
            (2, 4): j("related", 0.7),
            (3, 4): j("related", 0.8),
        }
    )
    assert s.duplicate.predicted == 2 and s.duplicate.true_positives == 1 and s.duplicate.precision == 0.5
    assert s.merge_grade.predicted == 2 and s.merge_grade.precision == 0.5
    assert s.false_merges == [(1, 3)]  # a confident duplicate verdict on a pair that is not one
    assert s.related.predicted == 2 and s.related.true_positives == 1
    assert {(a, b) for a, b, *_ in s.false_links} == {(1, 3), (2, 4)}


def test_an_unsure_duplicate_is_linked_but_not_merge_grade():
    s = run({(1, 2): j("duplicate", 0.7)})  # between the link and merge thresholds: a person decides
    assert s.duplicate.recall == 1.0 and s.merge_grade.recall == 0.0 and s.merge_grade.predicted == 0
    assert s.false_merges == []


def test_below_threshold_verdicts_are_not_links():
    s = run({(1, 2): j("duplicate", 0.5)})
    assert s.duplicate.predicted == 0 and s.confusion["duplicate"]["none"] == 1
    assert (1, 2, "duplicate", "judged duplicate at 0.50") in s.misses


def test_both_directions_of_a_pair_keep_the_more_confident_judgement():
    best = best_judgements([((1, 2), j("related", 0.5)), ((1, 2), j("duplicate", 0.9)), ((1, 2), j("distinct", 0.2))])
    assert best == {(1, 2): j("duplicate", 0.9)}
    assert pair(5, 2) == pair(2, 5) == (2, 5)


def test_f1_is_zero_when_nothing_predicted_was_right():
    s = run({(1, 3): j("duplicate", 0.9)})
    assert (s.duplicate.precision, s.duplicate.recall, s.duplicate.f1) == (0.0, 0.0, 0.0)


def test_the_real_labels_file_yields_the_expected_truth(session_factory):
    labels = json.loads(seed_module.LABELS_PATH.read_text(encoding="utf-8"))
    from sqlalchemy import select

    from core.models import Card

    with session_factory() as session:
        seed_module.seed(session)
        boards = {c.id: c.board_id for c in session.scalars(select(Card))}
    truth = truth_labels(labels, boards)
    counts = {k: sum(1 for v in truth.values() if v == k) for k in ("duplicate", "related", "distinct")}
    assert counts["duplicate"] == 7 and counts["related"] == 3
    assert sum(counts.values()) == 153 + 66  # C(18,2) on the first board, C(12,2) on the second
