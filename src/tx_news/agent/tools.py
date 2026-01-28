# Input: Postgres/Qdrant/embedding 模型与查询参数
# Output: search/list/timeline/profile 等工具方法返回结构化结果
# Pos: Agent 工具实现层（变更时同步更新以上注释与所属目录 FOLDER.md；并在主数据缺失时做本地缓存引导）

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import desc, func, select

from tx_news.db import Analysis, Article, ArticleVersion
from tx_news.embedding.embedder import DEFAULT_EMBEDDING_MODEL, Embedder, build_embedder
from tx_news.settings import get_settings
from tx_news.storage.postgres import (
    bootstrap_a_share_basic_from_cache,
    get_a_share,
    get_analysis,
    get_article,
    get_event_canonical_ids,
    get_latest_version,
    init_db,
    list_signals,
    make_engine,
    session_scope,
)
from tx_news.storage.qdrant import QdrantStore
from tx_news.storage.qdrant import scored_point_canonical_id
from tx_news.kg.ids import node_id_event, node_id_ticker, point_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def _get_engine():
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    return engine


@lru_cache(maxsize=1)
def _get_embedding_runtime() -> tuple[Embedder, str, str]:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
    embedder, qdrant_strategy = build_embedder(embedding_cfg)
    model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
    return embedder, qdrant_strategy, model_name


@lru_cache(maxsize=1)
def _get_qdrant() -> QdrantStore:
    settings = get_settings()
    return QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection)


def _get_event_memory_qdrant() -> QdrantStore:
    settings = get_settings()
    return QdrantStore(url=settings.qdrant_url, collection="txnews_event_memory")


def _get_entity_memory_qdrant() -> QdrantStore:
    settings = get_settings()
    return QdrantStore(url=settings.qdrant_url, collection="txnews_entity_memory")


def _get_edge_memory_qdrant() -> QdrantStore:
    settings = get_settings()
    return QdrantStore(url=settings.qdrant_url, collection="txnews_edge_memory")


@dataclass
class ToolContext:
    embedder: Embedder
    qdrant: QdrantStore
    event_qdrant: QdrantStore
    entity_qdrant: QdrantStore
    edge_qdrant: QdrantStore
    qdrant_strategy: str
    embedding_model_name: str

    def __post_init__(self) -> None:
        self.engine = _get_engine()


class TxNewsTools:
    def __init__(self) -> None:
        embedder, qdrant_strategy, model_name = _get_embedding_runtime()
        self.ctx = ToolContext(
            embedder=embedder,
            qdrant=_get_qdrant(),
            event_qdrant=_get_event_memory_qdrant(),
            entity_qdrant=_get_entity_memory_qdrant(),
            edge_qdrant=_get_edge_memory_qdrant(),
            qdrant_strategy=qdrant_strategy,
            embedding_model_name=model_name,
        )

    def search_news(self, *, q: str, limit: int = 10) -> list[dict[str, Any]]:
        vector = self.ctx.embedder.embed(q[:2000])
        qdrant = self.ctx.qdrant.resolve_collection_for_embedding(
            vector_size=len(vector),
            model_name_or_path=self.ctx.embedding_model_name,
            strategy=self.ctx.qdrant_strategy,
        )
        self.ctx.qdrant = qdrant
        points = qdrant.search(vector=vector, limit=int(limit))
        out: list[dict[str, Any]] = []
        for p in points:
            cid = scored_point_canonical_id(p) or str(p.id)
            a = get_article(self.ctx.engine, cid)
            v = get_latest_version(self.ctx.engine, cid)
            an = get_analysis(self.ctx.engine, cid)
            if not a:
                continue
            out.append(
                {
                    "canonical_id": cid,
                    "score": float(p.score or 0.0),
                    "title": a.title,
                    "url": v.url if v else None,
                    "source_id": v.source_id if v else None,
                    "published_at": v.published_at.isoformat() if v and v.published_at else None,
                    "event_type": an.event_type if an else None,
                    "event_id": (an.data.get("event_id") if an and isinstance(an.data, dict) else None),
                    "tickers": (an.data.get("tickers") if an and isinstance(an.data, dict) else []) or [],
                }
            )
        return out

    def list_recent(self, *, minutes: int = 180, limit: int = 30) -> list[dict[str, Any]]:
        cutoff = utcnow() - timedelta(minutes=int(minutes))
        # Use ORM Session execution to ensure rows unpack as (ArticleVersion, Article, Analysis).
        with session_scope(self.ctx.engine) as s:
            rows = s.execute(
                select(ArticleVersion, Article, Analysis)
                .join(Article, Article.canonical_id == ArticleVersion.canonical_id)
                .outerjoin(Analysis, Analysis.canonical_id == ArticleVersion.canonical_id)
                .where(func.coalesce(ArticleVersion.published_at, ArticleVersion.fetched_at) >= cutoff)
                .order_by(desc(func.coalesce(ArticleVersion.published_at, ArticleVersion.fetched_at)))
                .limit(int(limit))
            ).all()

        out: list[dict[str, Any]] = []
        for v, a, an in rows:
            out.append(
                {
                    "canonical_id": a.canonical_id,
                    "title": a.title,
                    "url": v.url,
                    "source_id": v.source_id,
                    "published_at": (v.published_at or v.fetched_at).isoformat() if (v.published_at or v.fetched_at) else None,
                    "event_type": an.event_type if an else None,
                    "event_id": (an.data.get("event_id") if an and isinstance(an.data, dict) else None),
                    "tickers": (an.data.get("tickers") if an and isinstance(an.data, dict) else []) or [],
                }
            )
        return out

    def get_article_analysis(self, *, canonical_id: str) -> dict[str, Any]:
        a = get_article(self.ctx.engine, canonical_id)
        if not a:
            return {"error": "not_found"}
        v = get_latest_version(self.ctx.engine, canonical_id)
        an = get_analysis(self.ctx.engine, canonical_id)
        return {
            "canonical_id": canonical_id,
            "title": a.title,
            "latest_url": v.url if v else None,
            "source_id": v.source_id if v else None,
            "published_at": v.published_at.isoformat() if v and v.published_at else None,
            "analysis": an.data if an else None,
        }

    def list_signals(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = list_signals(self.ctx.engine, limit=int(limit))
        return [
            {
                "canonical_id": r.canonical_id,
                "kind": r.kind,
                "data": r.data,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    def get_event_timeline(self, *, event_id: str, limit: int = 50) -> list[dict[str, Any]]:
        canonical_ids = get_event_canonical_ids(self.ctx.engine, event_id, limit=int(limit))
        out: list[dict[str, Any]] = []
        for cid in canonical_ids:
            out.append(self.get_article_analysis(canonical_id=cid))
        return out

    def get_entity_profile(self, *, ts_code: str) -> dict[str, Any]:
        row = get_a_share(self.ctx.engine, ts_code)
        if not row:
            bootstrap = bootstrap_a_share_basic_from_cache(self.ctx.engine)
            row = get_a_share(self.ctx.engine, ts_code)
            if not row:
                return {"error": "not_found", "bootstrap": bootstrap}
        return {
            "ts_code": row.ts_code,
            "name": row.name,
            "industry": row.industry,
            "area": row.area,
            "market": row.market,
            "list_date": row.list_date,
            "aliases": row.aliases,
            "updated_at": row.updated_at.isoformat(),
        }

    def search_entities(self, *, q: str, limit: int = 10) -> list[dict[str, Any]]:
        vector = self.ctx.embedder.embed(q[:2000])
        qdrant = self.ctx.entity_qdrant
        # v2 uses fixed collections; ensure_collection happens inside search().
        try:
            points = qdrant.search(vector=vector, limit=int(limit), filter_payload={"graph_env": "prod"})
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for p in points:
            payload = getattr(p, "payload", None)
            if not isinstance(payload, dict):
                continue
            out.append(
                {
                    "node_id": payload.get("node_id"),
                    "ts_code": payload.get("ts_code"),
                    "name": payload.get("name"),
                    "industry": payload.get("industry"),
                    "score": float(p.score or 0.0),
                    "updated_at_ts": payload.get("updated_at_ts"),
                }
            )
        return out

    def search_events(self, *, q: str, limit: int = 10, recent_hours: int = 72) -> list[dict[str, Any]]:
        vector = self.ctx.embedder.embed(q[:2000])
        qdrant = self.ctx.event_qdrant
        try:
            points = qdrant.search(vector=vector, limit=int(limit), filter_payload={"graph_env": "prod"})
        except Exception:
            return []
        cutoff = utcnow() - timedelta(hours=int(recent_hours))
        cutoff_ts = int(cutoff.timestamp())

        out: list[dict[str, Any]] = []
        for p in points:
            payload = getattr(p, "payload", None)
            if not isinstance(payload, dict):
                continue
            last_ts = int(payload.get("last_published_at_ts") or 0)
            if last_ts and last_ts < cutoff_ts:
                continue
            event_id = str(payload.get("event_id") or "").strip()
            if not event_id:
                continue
            cids = payload.get("canonical_ids") if isinstance(payload.get("canonical_ids"), list) else []
            evidence: list[dict[str, Any]] = []
            for cid in [str(x) for x in cids[:8]]:
                a = get_article(self.ctx.engine, cid)
                v = get_latest_version(self.ctx.engine, cid)
                if not a:
                    continue
                evidence.append(
                    {
                        "canonical_id": cid,
                        "title": a.title,
                        "url": v.url if v else None,
                        "published_at": v.published_at.isoformat() if v and v.published_at else None,
                        "source_id": v.source_id if v else None,
                    }
                )
            out.append(
                {
                    "node_id": payload.get("node_id"),
                    "event_id": event_id,
                    "event_type": payload.get("event_type"),
                    "snapshot_text": payload.get("snapshot_text"),
                    "score": float(p.score or 0.0),
                    "evidence": evidence,
                }
            )
        return out

    def get_event_neighbors(self, *, event_id: str, limit: int = 10) -> list[dict[str, Any]]:
        src = node_id_event(event_id)
        qdrant = self.ctx.edge_qdrant
        # Scan edges by payload filter (non-vector).
        recs: list[Any] = []
        offset = None
        while True:
            try:
                pts, offset2 = qdrant.scroll(
                    filter_payload={"graph_env": "prod", "src": src, "relation": "related_to"},
                    limit=256,
                    with_payload=True,
                    with_vectors=False,
                    offset=offset,
                )
            except Exception:
                return []
            recs.extend(pts)
            if not offset2:
                break
            offset = offset2

        edges: list[dict[str, Any]] = []
        for r in recs:
            payload = getattr(r, "payload", None)
            if not isinstance(payload, dict):
                continue
            edges.append(payload)
        edges.sort(key=lambda x: float(x.get("weight") or 0.0), reverse=True)
        edges = edges[: int(limit)]

        # Fetch neighbor event snapshots.
        node_ids = [str(e.get("dst") or "") for e in edges if str(e.get("dst") or "").startswith("event:")]
        pids = [point_id(graph_env="prod", item_id=nid) for nid in node_ids]
        by_node_id: dict[str, dict[str, Any]] = {}
        if pids:
            for r in self.ctx.event_qdrant.retrieve(point_ids=pids, with_payload=True, with_vectors=False):
                payload = getattr(r, "payload", None)
                if isinstance(payload, dict) and payload.get("node_id"):
                    by_node_id[str(payload["node_id"])] = payload

        out: list[dict[str, Any]] = []
        for e in edges:
            dst = str(e.get("dst") or "")
            ev = by_node_id.get(dst) or {}
            out.append(
                {
                    "dst_event_id": ev.get("event_id") or dst.replace("event:", ""),
                    "dst_event_type": ev.get("event_type"),
                    "dst_snapshot_text": ev.get("snapshot_text"),
                    "weight": e.get("weight"),
                    "confidence": e.get("confidence"),
                    "reason_text": e.get("reason_text"),
                    "evidence_canonical_ids": e.get("evidence_canonical_ids") or [],
                }
            )
        return out

    def explain_connection(self, *, event_a: str, event_b: str) -> dict[str, Any]:
        src = node_id_event(event_a)
        dst = node_id_event(event_b)
        qdrant = self.ctx.edge_qdrant

        def _find(s: str, d: str) -> dict[str, Any] | None:
            try:
                pts, _ = qdrant.scroll(
                    filter_payload={"graph_env": "prod", "src": s, "dst": d, "relation": "related_to"},
                    limit=1,
                    with_payload=True,
                    with_vectors=False,
                    offset=None,
                )
            except Exception:
                return None
            if not pts:
                return None
            payload = getattr(pts[0], "payload", None)
            return payload if isinstance(payload, dict) else None

        edge = _find(src, dst) or _find(dst, src)
        if not edge:
            return {"error": "not_found"}
        return {
            "src": edge.get("src"),
            "dst": edge.get("dst"),
            "relation": edge.get("relation"),
            "weight": edge.get("weight"),
            "confidence": edge.get("confidence"),
            "reason_text": edge.get("reason_text"),
            "evidence_canonical_ids": edge.get("evidence_canonical_ids") or [],
        }
