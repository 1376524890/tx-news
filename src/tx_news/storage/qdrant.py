from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm


@dataclass(frozen=True)
class QdrantStore:
    url: str
    collection: str

    def _client(self) -> QdrantClient:
        return QdrantClient(url=self.url)

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

    def upsert(
        self,
        *,
        point_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        self.ensure_collection(len(vector))
        self._client().upsert(
            collection_name=self.collection,
            points=[
                qm.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
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
        return self._client().search(
            collection_name=self.collection,
            query_vector=vector,
            query_filter=qfilter,
            limit=limit,
            with_payload=True,
        )

