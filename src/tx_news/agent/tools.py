# Input: Postgres/Qdrant/embedding 模型与查询参数
# Output: search/list/timeline/profile 等工具方法返回结构化结果
# Pos: Agent 工具实现层（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import desc, func, select

from tx_news.db import Analysis, Article, ArticleVersion
from tx_news.embedding.embedder import Embedder
from tx_news.settings import get_settings
from tx_news.storage.postgres import (
    get_a_share,
    get_analysis,
    get_article,
    get_event_canonical_ids,
    get_latest_version,
    init_db,
    list_signals,
    make_engine,
)
from tx_news.storage.qdrant import QdrantStore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def _get_engine():
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    return engine


@lru_cache(maxsize=1)
def _get_embedder() -> Embedder:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    model_name = (file_cfg.embedding or {}).get("model_name", "BAAI/bge-small-zh-v1.5")
    return Embedder(model_name_or_path=model_name)


@lru_cache(maxsize=1)
def _get_qdrant() -> QdrantStore:
    settings = get_settings()
    return QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection)


@dataclass
class ToolContext:
    embedder: Embedder
    qdrant: QdrantStore

    def __post_init__(self) -> None:
        self.engine = _get_engine()


class TxNewsTools:
    def __init__(self) -> None:
        self.ctx = ToolContext(
            embedder=_get_embedder(),
            qdrant=_get_qdrant(),
        )

    def search_news(self, *, q: str, limit: int = 10) -> list[dict[str, Any]]:
        vector = self.ctx.embedder.embed(q[:2000])
        points = self.ctx.qdrant.search(vector=vector, limit=int(limit))
        out: list[dict[str, Any]] = []
        for p in points:
            cid = str(p.id)
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
        with self.ctx.engine.connect() as conn:
            rows = conn.execute(
                select(ArticleVersion, Article, Analysis)
                .join(Article, Article.canonical_id == ArticleVersion.canonical_id)
                .outerjoin(Analysis, Analysis.canonical_id == ArticleVersion.canonical_id)
                .where(
                    func.coalesce(ArticleVersion.published_at, ArticleVersion.fetched_at) >= cutoff
                )
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
            return {"error": "not_found"}
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
