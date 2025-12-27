from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import FastAPI, Query
from pydantic import BaseModel

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


app = FastAPI(title="tx-news API", version="0.1.0")


class SearchHit(BaseModel):
    canonical_id: str
    score: float
    title: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    event_type: str | None = None
    tickers: list[dict[str, Any]] = []


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/search", response_model=list[SearchHit])
def search(q: str = Query(min_length=1), limit: int = Query(default=10, ge=1, le=50)):
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    model_name = (file_cfg.embedding or {}).get("model_name", "bge-small-zh-v1.5")
    embedder = Embedder(model_name_or_path=model_name)
    vector = embedder.embed(q[:2000])

    qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection)
    points = qdrant.search(vector=vector, limit=limit)

    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    hits: list[SearchHit] = []
    for p in points:
        cid = str(p.id)
        a = get_article(engine, cid)
        v = get_latest_version(engine, cid)
        an = get_analysis(engine, cid)
        if not a:
            continue
        hits.append(
            SearchHit(
                canonical_id=cid,
                score=float(p.score or 0.0),
                title=a.title,
                url=v.url if v else None,
                published_at=v.published_at if v else None,
                event_type=an.event_type if an else None,
                tickers=(an.data.get("tickers") if an else []) or [],
            )
        )
    return hits


@app.get("/articles/{canonical_id}")
def article(canonical_id: str) -> dict[str, Any]:
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    a = get_article(engine, canonical_id)
    if not a:
        return {"error": "not_found"}
    v = get_latest_version(engine, canonical_id)
    an = get_analysis(engine, canonical_id)
    # do not return article.text (版权)
    return {
        "canonical_id": canonical_id,
        "title": a.title,
        "latest_url": v.url if v else None,
        "published_at": v.published_at.isoformat() if v and v.published_at else None,
        "analysis": an.data if an else None,
    }


@app.get("/signals")
def signals(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    rows = list_signals(engine, limit=limit)
    return [
        {
            "canonical_id": r.canonical_id,
            "kind": r.kind,
            "data": r.data,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@app.get("/events/{event_id}")
def event_timeline(event_id: str, limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    canonical_ids = get_event_canonical_ids(engine, event_id, limit=limit)
    out: list[dict[str, Any]] = []
    for cid in canonical_ids:
        a = get_article(engine, cid)
        if not a:
            continue
        v = get_latest_version(engine, cid)
        an = get_analysis(engine, cid)
        out.append(
            {
                "canonical_id": cid,
                "title": a.title,
                "url": v.url if v else None,
                "published_at": v.published_at.isoformat() if v and v.published_at else None,
                "analysis": an.data if an else None,
            }
        )
    return out


@app.get("/entities/{ts_code}")
def entity_profile(ts_code: str) -> dict[str, Any]:
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    row = get_a_share(engine, ts_code)
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
