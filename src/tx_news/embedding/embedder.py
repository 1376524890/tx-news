from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _normalize_model_name_or_path(model_name_or_path: str) -> str:
    """
    Normalize common shorthands / wrong repo ids to a valid HuggingFace repo id.

    We intentionally keep this small and opinionated to avoid surprising users.
    """
    s = (model_name_or_path or "").strip()
    if not s:
        return s

    # Local folder always wins (no network / no hub resolution).
    if Path(s).exists():
        return s

    # Common shorthands for Chinese CPU-friendly embeddings.
    if s in {"bge-small-zh-v1.5", "sentence-transformers/bge-small-zh-v1.5"}:
        return "BAAI/bge-small-zh-v1.5"

    return s


@dataclass
class Embedder:
    model_name_or_path: str

    _model = None

    def _load(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer

        resolved = _normalize_model_name_or_path(self.model_name_or_path)
        if resolved != self.model_name_or_path:
            logger.info("embedding model resolved: %s -> %s", self.model_name_or_path, resolved)
        else:
            logger.info("loading embedding model: %s", resolved)
        try:
            self._model = SentenceTransformer(resolved, device="cpu")
        except Exception as e:
            raise RuntimeError(
                "embedding 模型加载失败：请在 `config/config.yaml` 配置 `embedding.model_name` 为可用的本地模型目录，"
                "或使用公开模型 id（例如 `BAAI/bge-small-zh-v1.5`）。"
            ) from e
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._load()
        vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        return [float(x) for x in vec]
