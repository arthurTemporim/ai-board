"""similarity_mcp: local embeddings stored in SQLite and cosine nearest-neighbour search.

Vectors are float32 blobs in card_embedding (one row per card, latest model). Search loads the
board's vectors into numpy and ranks by dot product (the vectors are unit length). Results use the
vectors as of each card's last index_card call, so re-index a card after its text changes.
"""

from __future__ import annotations

import hashlib
from typing import Annotated

import numpy as np
from pydantic import BaseModel, Field

from ai.mcp_servers.common import CardId, new_server
from ai.mcp_servers.similarity_mcp.embedder import Embedder, get_embedder
from core.db import session_scope
from core.errors import NotFoundError
from core.models import Card
from core.repositories.boards import BoardRepository
from core.repositories.cards import CardRepository
from core.repositories.suggestions import EmbeddingRepository

mcp = new_server(
    "similarity_mcp",
    "Index cards as local text embeddings and find the most similar cards. Deterministic; no LLM calls.",
)


class IndexResult(BaseModel):
    card_id: int
    model: str
    dim: int
    indexed: bool = Field(description="True when a new vector was computed and stored")
    unchanged: bool = Field(description="True when the stored vector already matched the card's current text")


class IndexBoardResult(BaseModel):
    board_id: int
    model: str
    total_cards: int
    indexed: int
    unchanged: int


class SimilarCard(BaseModel):
    card_id: int
    title: str
    column_id: int
    score: float = Field(description="Cosine similarity, -1 to 1; higher is more similar")


class FindSimilarResult(BaseModel):
    card_id: int
    model: str
    candidates_considered: int = Field(description="Other cards on the board that have a vector")
    unindexed_cards: int = Field(description="Cards on the board with no vector yet; run index_board to include them")
    results: list[SimilarCard]


def card_text(card: Card, feedback_quotes: list[str]) -> str:
    return "\n".join([card.title, card.description, *feedback_quotes]).strip()


def _text_hash(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def _index(session, card: Card, embedder: Embedder) -> tuple[bool, int]:
    """Store the card's vector if missing or out of date. Returns (wrote, dim)."""
    repo = EmbeddingRepository(session)
    quotes = [f.quote for f in CardRepository(session).feedback(card.id)]
    text = card_text(card, quotes)
    digest = _text_hash(embedder.name, text)
    row = repo.get(card.id)
    if row is not None and row.model == embedder.name and row.text_hash == digest:
        return False, row.dim
    vector = embedder.embed([text])[0].astype("<f4")
    repo.upsert(card.id, embedder.name, int(vector.shape[0]), vector.tobytes(), digest)
    return True, int(vector.shape[0])


def _card(session, card_id: int) -> Card:
    card = CardRepository(session).get(card_id)
    if card is None:
        raise NotFoundError(f"Card {card_id} not found")
    return card


@mcp.tool()
def index_card(card_id: CardId) -> IndexResult:
    """Compute and store the embedding of a card's title, description and feedback quotes. Skips the work if the stored vector already matches the current text. Writes only to the embedding table."""
    embedder = get_embedder()
    with session_scope() as session:
        wrote, dim = _index(session, _card(session, card_id), embedder)
        return IndexResult(card_id=card_id, model=embedder.name, dim=dim, indexed=wrote, unchanged=not wrote)


@mcp.tool()
def index_board(board_id: Annotated[int, Field(ge=1, description="Board id")]) -> IndexBoardResult:
    """Index every card on a board (same as calling index_card for each). Writes only to the embedding table."""
    embedder = get_embedder()
    with session_scope() as session:
        if BoardRepository(session).get(board_id) is None:
            raise NotFoundError(f"Board {board_id} not found")
        cards = CardRepository(session).for_board(board_id)
        wrote = sum(1 for card in cards if _index(session, card, embedder)[0])
        return IndexBoardResult(
            board_id=board_id, model=embedder.name, total_cards=len(cards), indexed=wrote, unchanged=len(cards) - wrote
        )


@mcp.tool()
def find_similar_cards(
    card_id: CardId,
    top_k: Annotated[int, Field(ge=1, le=50, description="Maximum number of results")] = 5,
    min_score: Annotated[float, Field(ge=-1.0, le=1.0, description="Drop results scoring below this")] = 0.0,
) -> FindSimilarResult:
    """Find the cards on the same board most similar to this one by cosine similarity of their embeddings, best first (ties by card id). The query card is indexed first if needed; other cards must already be indexed. Read-only apart from that."""
    embedder = get_embedder()
    with session_scope() as session:
        card = _card(session, card_id)
        _index(session, card, embedder)
        repo = EmbeddingRepository(session)
        rows = repo.for_board(card.board_id, embedder.name)
        own = next(r for r in rows if r.card_id == card_id)
        others = [r for r in rows if r.card_id != card_id and r.dim == own.dim]
        results: list[SimilarCard] = []
        if others:
            matrix = np.stack([np.frombuffer(r.vector, dtype="<f4") for r in others])
            scores = matrix @ np.frombuffer(own.vector, dtype="<f4")
            ids = np.array([r.card_id for r in others])
            order = np.lexsort((ids, -scores))  # best score first, then lower id
            cards = CardRepository(session)
            for index in order:
                if len(results) >= top_k or scores[index] < min_score:
                    break
                other = cards.get(int(ids[index]))
                if other is not None:
                    results.append(
                        SimilarCard(
                            card_id=other.id,
                            title=other.title,
                            column_id=other.column_id,
                            score=round(float(scores[index]), 4),
                        )
                    )
        return FindSimilarResult(
            card_id=card_id,
            model=embedder.name,
            candidates_considered=len(others),
            unindexed_cards=repo.count_cards_without(card.board_id, embedder.name),
            results=results,
        )
