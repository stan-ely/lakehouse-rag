"""Query embeddings with the same model the indexer used for passages."""

from typing import Any

import numpy as np
from numpy.typing import NDArray


class FastQueryEmbedder:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: Any = None

    def load(self) -> None:
        """Loads the ONNX model eagerly, so /ready reflects it and the first query is not slow."""
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def embed_query(self, text: str) -> NDArray[np.float32]:
        self.load()
        vector = next(iter(self._model.query_embed(text)))
        return np.asarray(vector, dtype=np.float32)
