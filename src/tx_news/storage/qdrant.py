# Input: Qdrant url、collection 与向量/payload
# Output: collection 初始化、向量 upsert 与 search 结果
# Pos: Qdrant 访问封装（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

logger = logging.getLogger(__name__)

QDRANT_POINT_ID_NAMESPACE = uuid.UUID("b1b0c707-9e02-4be5-9a64-3f60f2de15cf")


def _slugify_model_name(model_name_or_path: str) -> str:
    s = (model_name_or_path or "").strip()
    if not s:
        return "model"
    try:
        p = Path(s)
        if p.exists():
            s = p.name
    except OSError:
        pass
    s = re.sub(r"[^0-9A-Za-z_-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:48] or "model")


def embedding_collection_name(*, base: str, model_name_or_path: str, vector_size: int) -> str:
    slug = _slugify_model_name(model_name_or_path)
    return f"{base}__{slug}__{int(vector_size)}"


def scored_point_canonical_id(p: qm.ScoredPoint) -> str | None:
    payload = getattr(p, "payload", None)
    if isinstance(payload, dict):
        cid = payload.get("canonical_id")
        if cid:
            return str(cid)
    return None


@dataclass(frozen=True)
class QdrantStore:
    url: str
    collection: str

    def _client(self) -> QdrantClient:
        return QdrantClient(url=self.url)

    def resolve_collection_for_embedding(
        self,
        *,
        vector_size: int,
        model_name_or_path: str,
        strategy: str = "auto",
    ) -> "QdrantStore":
        """
        Resolve collection name when embedding dimension changes.

        strategy:
          - "base": always use Settings.qdrant_collection
          - "scoped": always use a model/dim-scoped collection
          - "auto": use base if compatible, otherwise fall back to scoped
        """
        strategy = (strategy or "auto").strip().lower()
        if strategy not in {"auto", "base", "scoped"}:
            raise ValueError(f"invalid qdrant_collection_strategy: {strategy}")

        base = self.collection
        scoped = embedding_collection_name(
            base=base, model_name_or_path=model_name_or_path, vector_size=vector_size
        )
        if strategy == "base":
            return self
        if strategy == "scoped":
            return QdrantStore(url=self.url, collection=scoped)

        c = self._client()
        existing = {col.name for col in c.get_collections().collections}

        def _size(name: str) -> int | None:
            if name not in existing:
                return None
            info = c.get_collection(name)
            return int(info.config.params.vectors.size)  # type: ignore[union-attr]

        base_size = _size(base)
        if base_size == int(vector_size):
            return self
        scoped_size = _size(scoped)
        if scoped_size == int(vector_size):
            return QdrantStore(url=self.url, collection=scoped)

        # When base exists but mismatched, we must switch to a scoped collection.
        if base_size is not None and base_size != int(vector_size):
            logger.warning(
                "Qdrant collection vector size mismatch: %s (%s) != %s; switch to %s",
                base,
                base_size,
                int(vector_size),
                scoped,
            )
            return QdrantStore(url=self.url, collection=scoped)

        return self

    def ensure_collection(self, vector_size: int) -> None:
        c = self._client()
        existing = {col.name for col in c.get_collections().collections}
        if self.collection in existing:
            info = c.get_collection(self.collection)
            current = info.config.params.vectors.size  # type: ignore[union-attr]
            if current != vector_size:
                raise ValueError(f"Qdrant collection vector size mismatch: {current} != {vector_size}")
            return
        c.create_collection(
            collection_name=self.collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    def to_point_id(self, point_id: str) -> uuid.UUID:
        """
        Qdrant server accepts only `int` or `uuid` point ids; it does NOT accept arbitrary strings.
        We use a deterministic uuid5 derived from the caller-provided id (usually canonical_id).
        """
        s = str(point_id or "").strip()
        if not s:
            raise ValueError("point_id is empty")
        try:
            return uuid.UUID(s)
        except Exception:
            return uuid.uuid5(QDRANT_POINT_ID_NAMESPACE, s)

    def upsert(
        self,
        *,
        point_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        self.ensure_collection(len(vector))
        qid = self.to_point_id(point_id)
        payload2 = dict(payload or {})
        payload2.setdefault("canonical_id", point_id)
        payload2.setdefault("qdrant_point_id", str(qid))
        self._client().upsert(
            collection_name=self.collection,
            points=[
                qm.PointStruct(
                    id=qid,
                    vector=vector,
                    payload=payload2,
                )
            ],
        )

    def search(
        self,
        *,
        vector: list[float],
        limit: int = 10,
        filter_payload: dict[str, Any] | None = None,
    ) -> list[qm.ScoredPoint]:
        self.ensure_collection(len(vector))
        qfilter = None
        if filter_payload:
            must = []
            for k, v in filter_payload.items():
                must.append(qm.FieldCondition(key=k, match=qm.MatchValue(value=v)))
            qfilter = qm.Filter(must=must)
        c = self._client()
        # qdrant-client >= 1.13 removed `search` in favor of `query_points`.
        if hasattr(c, "query_points"):
            resp = c.query_points(
                collection_name=self.collection,
                query=vector,
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            )
            return list(resp.points or [])
        # Legacy fallback for older clients.
        if hasattr(c, "search"):
            return c.search(  # type: ignore[no-any-return]
                collection_name=self.collection,
                query_vector=vector,
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            )
        raise RuntimeError("qdrant-client API mismatch: neither query_points nor search is available")
