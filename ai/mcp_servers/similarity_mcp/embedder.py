"""Text embedders. All return L2-normalised float32 vectors, so cosine similarity is a dot product.

LiteLLMEmbedder calls the embedding models behind a litellm proxy (/v1/embeddings). It is the
default whenever LITELLM_BASE_URL is set.
FastEmbedder runs an ONNX model inside this process (no proxy, no key; downloads the model once).
HashEmbedder is the deterministic stand-in used when MOCK_AI=1: a signed bag-of-words hashed into
a fixed number of buckets, so texts that share words still score as similar.

Choose with EMBEDDING_PROVIDER (litellm | fastembed) and EMBEDDING_MODEL.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import tempfile
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Protocol

import httpx
import numpy as np

from ai.mcp_servers.common import mock_ai

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # fastembed
DEFAULT_LITELLM_MODEL = "nomic-embed-text-v2-moe:latest"  # litellm
_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "that", "with", "this", "our", "are", "can", "not", "all", "from", "have", "your", "you"}


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return a (len(texts), dim) float32 array of unit vectors (zeros for empty text)."""


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class HashEmbedder:
    name = "mock-hash-256"
    dim = 256

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in _WORD.findall(text.lower()):
                if word in _STOP:
                    continue
                digest = hashlib.sha256(word.encode("utf-8")).digest()
                out[row, int.from_bytes(digest[:4], "big") % self.dim] += 1.0 if digest[4] & 1 else -1.0
        return _normalise(out)


class FastEmbedder:
    """fastembed's TextEmbedding; the model is downloaded on first use into the cache directory."""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: str | None = None):
        self.name = model_name
        self._cache_dir = (
            cache_dir or os.environ.get("FASTEMBED_CACHE_DIR") or os.path.join(tempfile.gettempdir(), "fastembed")
        )
        self._model = None
        self._dim: int | None = None

    def _load(self):
        if self._model is None:
            # The model downloader keeps state under HF_HOME, which defaults to ~/.cache and fails
            # when the process has no writable home (as in the container). Keep it with the model.
            os.environ.setdefault("HF_HOME", os.path.join(self._cache_dir, "hf"))
            from fastembed import TextEmbedding  # imported lazily: it pulls in onnxruntime

            self._model = TextEmbedding(model_name=self.name, cache_dir=self._cache_dir)
        return self._model

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = int(self.embed(["dimension probe"]).shape[1])
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.asarray(list(self._load().embed(texts)), dtype=np.float32)
        self._dim = int(vectors.shape[1])
        return _normalise(vectors)


class EmbeddingError(RuntimeError):
    """The embedding service failed or returned something unusable."""


class LiteLLMEmbedder:
    """Embeddings from an OpenAI-compatible /v1/embeddings endpoint.

    Uses plain HTTP rather than the OpenAI SDK: the SDK always sends an `encoding_format`
    parameter, and litellm rejects it for Ollama-backed models.
    """

    def __init__(
        self,
        model: str = DEFAULT_LITELLM_MODEL,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        batch_size: int = 16,
        max_attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        url = (base_url if base_url is not None else os.environ.get("LITELLM_BASE_URL") or "").rstrip("/")
        if not url:
            raise EmbeddingError("LITELLM_BASE_URL is not set, so the litellm embedder has nowhere to connect")
        self.name = model
        self._url = (url if url.endswith("/v1") else url + "/v1") + "/embeddings"
        key = api_key if api_key is not None else os.environ.get("LITELLM_API_KEY")
        self._headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._timeout = timeout if timeout is not None else float(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "120"))
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._transport = transport
        self._sleep = sleep
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = int(self.embed(["dimension probe"]).shape[1])
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        rows: list[list[float]] = []
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            for start in range(0, len(texts), self._batch_size):
                # An empty string is rejected by some backends; a space embeds as "nothing".
                batch = [text or " " for text in texts[start : start + self._batch_size]]
                rows.extend(self._request(client, batch))
        vectors = np.asarray(rows, dtype=np.float32)
        if vectors.ndim != 2 or len(vectors) != len(texts):
            raise EmbeddingError(f"Embedding service returned {len(rows)} vectors for {len(texts)} texts")
        self._dim = int(vectors.shape[1])
        return _normalise(vectors)

    def _request(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        last = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = client.post(self._url, headers=self._headers, json={"model": self.name, "input": batch})
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    try:
                        data = sorted(response.json()["data"], key=lambda item: item["index"])
                        return [item["embedding"] for item in data]
                    except (KeyError, TypeError, ValueError) as exc:
                        raise EmbeddingError(f"Unexpected embeddings response from {self._url}: {exc}") from exc
                last = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in (408, 409, 429) and response.status_code < 500:
                    raise EmbeddingError(f"Embedding model '{self.name}' failed: {last}")
            if attempt < self._max_attempts:
                self._sleep(min(10.0, 2 ** (attempt - 1)) * (0.5 + 0.5 * random.random()))
        raise EmbeddingError(f"Embedding model '{self.name}' failed after {self._max_attempts} attempts: {last}")


def embedding_provider() -> str:
    """EMBEDDING_PROVIDER if set; otherwise litellm when a proxy is configured, else fastembed."""
    chosen = (os.environ.get("EMBEDDING_PROVIDER") or "").strip().lower()
    if chosen:
        if chosen not in ("litellm", "fastembed"):
            raise EmbeddingError(f"EMBEDDING_PROVIDER must be 'litellm' or 'fastembed', got '{chosen}'")
        return chosen
    return "litellm" if os.environ.get("LITELLM_BASE_URL") else "fastembed"


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    if mock_ai():
        return HashEmbedder()
    model = os.environ.get("EMBEDDING_MODEL")
    if embedding_provider() == "litellm":
        return LiteLLMEmbedder(model or DEFAULT_LITELLM_MODEL)
    return FastEmbedder(model or DEFAULT_MODEL)
