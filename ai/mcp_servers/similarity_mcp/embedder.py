"""Text embedders. Both return L2-normalised float32 vectors, so cosine similarity is a dot product.

FastEmbedder runs an ONNX model locally (no API key, no network after the first download).
HashEmbedder is the deterministic stand-in used when MOCK_AI=1: a signed bag-of-words hashed into
a fixed number of buckets, so texts that share words still score as similar.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from functools import lru_cache
from typing import Protocol

import numpy as np

from ai.mcp_servers.common import mock_ai

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
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


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    if mock_ai():
        return HashEmbedder()
    return FastEmbedder(os.environ.get("EMBEDDING_MODEL") or DEFAULT_MODEL)
