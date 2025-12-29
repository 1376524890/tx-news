# Input: embedding 模型名/路径 + device/cache 等配置 + 文本
# Output: 归一化向量 list[float]
# Pos: embedding 计算封装（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


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


def build_embedder(embedding_cfg: dict[str, Any] | None) -> tuple["Embedder", str]:
    """
    Build an Embedder from FileSettings.embedding.

    Returns:
      - embedder
      - qdrant_collection_strategy: "auto" | "base" | "scoped"
    """
    cfg = embedding_cfg or {}
    model_name = str(cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
    device = cfg.get("device")
    cache_dir = cfg.get("cache_dir")
    use_fp16 = bool(cfg.get("use_fp16", True))
    qdrant_strategy = str(cfg.get("qdrant_collection_strategy", "auto"))
    return (
        Embedder(
            model_name_or_path=model_name,
            device=str(device).strip() if device is not None else None,
            cache_dir=str(cache_dir).strip() if cache_dir is not None else None,
            use_fp16=use_fp16,
        ),
        qdrant_strategy,
    )


@dataclass
class Embedder:
    model_name_or_path: str
    device: str | None = None
    cache_dir: str | None = None
    use_fp16: bool = True

    _model: Any = field(default=None, init=False, repr=False, compare=False)

    _MODEL_CACHE: ClassVar[dict[tuple[str, str, str, bool], Any]] = {}

    def _resolve_device(self) -> str:
        d = (self.device or "").strip()
        if d and d.lower() != "auto":
            return d
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"

    def _load(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer

        resolved = _normalize_model_name_or_path(self.model_name_or_path)
        device = self._resolve_device()
        cache_key = (resolved, device, self.cache_dir or "", bool(self.use_fp16))
        cached = self._MODEL_CACHE.get(cache_key)
        if cached is not None:
            self._model = cached
            return cached
        if resolved != self.model_name_or_path:
            logger.info("embedding model resolved: %s -> %s", self.model_name_or_path, resolved)
        else:
            logger.info("loading embedding model: %s", resolved)
        try:
            kwargs: dict[str, Any] = {"device": device}
            if self.cache_dir:
                kwargs["cache_folder"] = self.cache_dir
            model = SentenceTransformer(resolved, **kwargs)
            if self.use_fp16 and str(device).startswith("cuda"):
                try:
                    model = model.half()
                except Exception as e:
                    logger.warning("embedding model fp16 cast failed; continue with default dtype: %s", e)
            self._MODEL_CACHE[cache_key] = model
            self._model = model
        except Exception as e:
            raise RuntimeError(
                "embedding 模型加载失败：请在 `config/config.yaml` 配置 `embedding.model_name` 为可用的本地模型目录，"
                "或使用公开模型 id（例如 `BAAI/bge-small-zh-v1.5`）。若要使用 GPU，请安装 CUDA 版 PyTorch，"
                "并配置 `embedding.device: cuda` / `cuda:0`。"
            ) from e
        return self._model

    def dim(self) -> int:
        model = self._load()
        return int(model.get_sentence_embedding_dimension())

    def embed(self, text: str) -> list[float]:
        model = self._load()
        t = (text or "").strip()
        if not t:
            t = " "
        vec = model.encode([t], normalize_embeddings=True, show_progress_bar=False)[0]
        return [float(x) for x in vec]
