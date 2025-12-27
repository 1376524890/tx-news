from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Embedder:
    model_name_or_path: str

    _model = None

    def _load(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer

        logger.info("loading embedding model: %s", self.model_name_or_path)
        self._model = SentenceTransformer(self.model_name_or_path, device="cpu")
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._load()
        vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        return [float(x) for x in vec]

