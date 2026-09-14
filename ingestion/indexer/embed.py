"""Local CPU embeddings, identical in every environment so vectors stay comparable."""

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384  # must match vector(384) in migration 0003

Vector = NDArray[np.float32]


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


class FastEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, batch_size: int = 32) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._model: Any = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        if self._model is None:
            # Loads onnxruntime and the model only when something actually needs embedding.
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)
        vectors = [
            np.asarray(vector, dtype=np.float32)
            for vector in self._model.passage_embed(list(texts), batch_size=self._batch_size)
        ]
        if vectors and vectors[0].shape != (EMBEDDING_DIM,):
            raise ValueError(f"{self._model_name} returned {vectors[0].shape}, expected 384-d")
        return vectors
