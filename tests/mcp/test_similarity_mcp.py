from __future__ import annotations

import os

import numpy as np
import pytest
from sqlalchemy import select

from ai.mcp_servers.similarity_mcp import embedder as embedder_module
from ai.mcp_servers.similarity_mcp.embedder import FastEmbedder, HashEmbedder, get_embedder
from core.models import Card, CardEmbedding
from core.services.cards import CardService
from tests.mcp.conftest import SIMILARITY_MODULE, tools_for

pytestmark = pytest.mark.anyio


# ---- the embedders, in process ----------------------------------------------------------------
def test_hash_embedder_is_deterministic_and_unit_length():
    e = HashEmbedder()
    a = e.embed(["Support SAML single sign-on", "Dark mode"])
    b = e.embed(["Support SAML single sign-on", "Dark mode"])
    assert a.dtype == np.float32 and a.shape == (2, e.dim)
    assert np.array_equal(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0, atol=1e-5)


def test_hash_embedder_scores_shared_words_higher_and_handles_empty_text():
    e = HashEmbedder()
    saml, okta, dark, empty = e.embed(
        ["Support SAML single sign-on", "Single sign-on with Okta, SAML please", "Dark mode night theme", ""]
    )
    assert float(saml @ okta) > float(saml @ dark) + 0.2
    assert not empty.any()  # no words -> zero vector, not NaN
    assert float(empty @ saml) == 0.0


def test_mock_ai_selects_the_hash_embedder_and_real_selects_fastembed(monkeypatch):
    get_embedder.cache_clear()
    monkeypatch.setenv("MOCK_AI", "1")
    assert isinstance(get_embedder(), HashEmbedder)
    get_embedder.cache_clear()
    monkeypatch.setenv("MOCK_AI", "0")
    monkeypatch.setenv("EMBEDDING_MODEL", "some/model")
    chosen = get_embedder()
    assert isinstance(chosen, FastEmbedder) and chosen.name == "some/model"
    get_embedder.cache_clear()


def test_fast_embedder_does_not_load_a_model_until_used(tmp_path):
    e = FastEmbedder("BAAI/bge-small-en-v1.5", cache_dir=str(tmp_path))
    assert e._model is None and e.name == "BAAI/bge-small-en-v1.5"
    assert not list(tmp_path.iterdir())  # nothing downloaded by constructing it


@pytest.mark.skipif(not os.environ.get("RUN_FASTEMBED"), reason="downloads a model; set RUN_FASTEMBED=1 to run")
def test_real_fastembed_model_ranks_paraphrases_above_unrelated_text(tmp_path):
    e = FastEmbedder(cache_dir=str(tmp_path))
    a, b, c = e.embed(["Support SAML single sign-on", "Let staff log in with Okta", "Dark mode for night shifts"])
    assert e.dim == 384 and np.isclose(np.linalg.norm(a), 1.0, atol=1e-4)
    assert float(a @ b) > float(a @ c)


def test_module_exposes_the_default_model_name():
    assert embedder_module.DEFAULT_MODEL == "BAAI/bge-small-en-v1.5"


# ---- the tools --------------------------------------------------------------------------------
def embedding_row(db, card_id):
    with db.session() as s:
        return s.get(CardEmbedding, card_id)


async def test_index_card_stores_a_normalised_float32_blob(db):
    async with tools_for(SIMILARITY_MODULE, db) as t:
        result = await t.ok("index_card", card_id=db.ids["saml"])
        assert (result["model"], result["dim"], result["indexed"], result["unchanged"]) == (
            "mock-hash-256",
            256,
            True,
            False,
        )
        again = await t.ok("index_card", card_id=db.ids["saml"])
        assert (again["indexed"], again["unchanged"]) == (False, True)
        assert "Card 999 not found" in await t.error("index_card", card_id=999)

    row = embedding_row(db, db.ids["saml"])
    assert (row.model, row.dim, len(row.vector)) == ("mock-hash-256", 256, 256 * 4)
    vector = np.frombuffer(row.vector, dtype="<f4")
    assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)
    assert len(row.text_hash) == 64


async def test_index_card_recomputes_only_when_the_text_changes(db):
    async with tools_for(SIMILARITY_MODULE, db) as t:
        await t.ok("index_card", card_id=db.ids["dark"])
        first = embedding_row(db, db.ids["dark"]).vector
        with db.session() as s:
            CardService(s).update_card(
                db.ids["dark"], {"description": "Completely different words about invoices and billing."}
            )
            s.commit()
        changed = await t.ok("index_card", card_id=db.ids["dark"])
    assert changed["indexed"] is True
    assert embedding_row(db, db.ids["dark"]).vector != first


async def test_feedback_quotes_are_part_of_the_indexed_text(db):
    async with tools_for(SIMILARITY_MODULE, db) as t:
        await t.ok("index_card", card_id=db.ids["saml"])  # has a feedback quote in the fixture
        with db.session() as s:
            from core.services.engagement import FeedbackService

            FeedbackService(s).add_feedback(db.ids["saml"], "Call", "Totally new quote about invoices", None)
            s.commit()
        assert (await t.ok("index_card", card_id=db.ids["saml"]))["indexed"] is True


async def test_index_board_indexes_every_card_once(db):
    async with tools_for(SIMILARITY_MODULE, db) as t:
        first = await t.ok("index_board", board_id=db.ids["board"])
        second = await t.ok("index_board", board_id=db.ids["board"])
        assert "Board 99 not found" in await t.error("index_board", board_id=99)
    assert (first["total_cards"], first["indexed"], first["unchanged"]) == (4, 4, 0)
    assert (second["indexed"], second["unchanged"]) == (0, 4)
    with db.session() as s:
        assert (
            s.scalar(select(Card.id).where(Card.id == db.ids["foreign"]))
            and embedding_row(db, db.ids["foreign"]) is None
        )


async def test_find_similar_cards_ranks_best_first_and_excludes_self_and_other_boards(db):
    ids = db.ids
    async with tools_for(SIMILARITY_MODULE, db) as t:
        await t.ok("index_board", board_id=ids["board"])
        await t.ok("index_card", card_id=ids["foreign"])  # identical words, but on another board
        found = await t.ok("find_similar_cards", card_id=ids["saml"], top_k=5)

    assert found["model"] == "mock-hash-256" and found["candidates_considered"] == 3
    results = found["results"]
    assert results[0]["card_id"] == ids["okta"] and results[0]["title"] == "Single sign-on with Okta for our staff"
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True) and scores[0] > 0.3
    returned = {r["card_id"] for r in results}
    assert ids["saml"] not in returned and ids["foreign"] not in returned
    assert {r["column_id"] for r in results} == {ids["inbox"]}


async def test_top_k_min_score_and_determinism(db):
    ids = db.ids
    async with tools_for(SIMILARITY_MODULE, db) as t:
        await t.ok("index_board", board_id=ids["board"])
        one = await t.ok("find_similar_cards", card_id=ids["saml"], top_k=1)
        assert [r["card_id"] for r in one["results"]] == [ids["okta"]]
        strict = await t.ok("find_similar_cards", card_id=ids["saml"], min_score=0.99)
        assert strict["results"] == [] and strict["candidates_considered"] == 3
        first = await t.ok("find_similar_cards", card_id=ids["csv"], top_k=3)
        second = await t.ok("find_similar_cards", card_id=ids["csv"], top_k=3)
        assert first == second
        assert "top_k" in await t.error("find_similar_cards", card_id=ids["saml"], top_k=0)
        assert "min_score" in await t.error("find_similar_cards", card_id=ids["saml"], min_score=2)
        assert "Card 999 not found" in await t.error("find_similar_cards", card_id=999)


async def test_find_similar_indexes_the_query_card_and_reports_what_is_missing(db):
    ids = db.ids
    async with tools_for(SIMILARITY_MODULE, db) as t:
        cold = await t.ok("find_similar_cards", card_id=ids["saml"])
        assert cold["candidates_considered"] == 0 and cold["unindexed_cards"] == 3 and cold["results"] == []
        assert embedding_row(db, ids["saml"]) is not None  # the query card itself was indexed
        await t.ok("index_board", board_id=ids["board"])
        warm = await t.ok("find_similar_cards", card_id=ids["saml"])
        assert warm["candidates_considered"] == 3 and warm["unindexed_cards"] == 0


async def test_ties_are_broken_by_card_id(db):
    with db.session() as s:
        cards = CardService(s)
        a = cards.create_card(board_id=db.ids["board"], title="Quarterly invoices archive", description="")
        b = cards.create_card(board_id=db.ids["board"], title="Quarterly invoices archive", description="")
        s.commit()
        first, second = sorted((a.id, b.id))
    async with tools_for(SIMILARITY_MODULE, db) as t:
        await t.ok("index_board", board_id=db.ids["board"])
        found = await t.ok("find_similar_cards", card_id=db.ids["csv"], top_k=50, min_score=-1)
        ordered = [r["card_id"] for r in found["results"] if r["card_id"] in (first, second)]
    assert ordered == [first, second]


@pytest.mark.skipif(not os.environ.get("RUN_FASTEMBED"), reason="downloads a model; set RUN_FASTEMBED=1 to run")
async def test_the_server_with_the_real_model_ranks_the_paraphrase_first(db, tmp_path):
    with db.session() as s:
        cards = CardService(s)
        okta = cards.create_card(
            board_id=db.ids["board"],
            title="Let staff log in with their Okta accounts",
            description="Nobody wants another password.",
        )
        s.commit()
    db.env.update({"MOCK_AI": "0", "FASTEMBED_CACHE_DIR": os.environ.get("FASTEMBED_CACHE_DIR", str(tmp_path / "fe"))})
    async with tools_for(SIMILARITY_MODULE, db) as t:
        indexed = await t.ok("index_board", board_id=db.ids["board"])
        found = await t.ok("find_similar_cards", card_id=db.ids["saml"], top_k=3)
    assert indexed["model"] == "BAAI/bge-small-en-v1.5" and found["model"] == indexed["model"]
    assert found["results"][0]["card_id"] in (okta.id, db.ids["okta"])  # both are sign-on requests
    assert found["results"][0]["card_id"] != db.ids["dark"]
    with db.session() as s:
        row = s.get(CardEmbedding, db.ids["saml"])
        assert (row.dim, len(row.vector)) == (384, 384 * 4)
