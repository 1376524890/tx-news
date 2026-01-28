# Input: HTTP 请求 + Postgres/Qdrant/Redis 等依赖 + 用户 Cookie（可选）+（可选）在线 LLM 配置
# Output: 公网/用户侧 API + 对话 UI（8000；在线 LLM 出错时可回退本地 vLLM）+ 看板 KG 反馈加权
# Pos: Public API 进程入口（变更时同步更新以上注释与所属目录 FOLDER.md；并在主数据缺失时尝试从本地缓存引导）

from __future__ import annotations

import json
import logging
import os
import time
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from redis import Redis
from sqlalchemy import text

from tx_news.agent.txnews_agent import AgentChatError, TxNewsAgent
from tx_news.embedding.embedder import DEFAULT_EMBEDDING_MODEL, build_embedder
from tx_news.logging import configure_logging
from tx_news.settings import get_settings
from tx_news.storage.postgres import (
    bootstrap_a_share_basic_from_cache,
    get_a_share,
    get_a_shares,
    get_analysis,
    get_article,
    get_event_canonical_ids,
    get_feedback_counts,
    get_latest_version,
    init_db,
    insert_feedback,
    make_engine,
)
from tx_news.storage.qdrant import QdrantStore, scored_point_canonical_id
from tx_news.user_llm_config import UID_COOKIE, clear_user_llm_config, get_user_llm_config, make_user_id, set_user_llm_config


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="tx-news API", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parents[2] / "apps" / "web" / "dist_public"

NO_STORE_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}

# Mount /assets for Vue SPA
if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")


@app.get("/vite.svg", include_in_schema=False)
def vite_svg() -> Response:
    f = STATIC_DIR / "vite.svg"
    if f.exists():
        return FileResponse(str(f))
    return Response(status_code=404)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _normalize_tickers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
            continue
        if isinstance(item, str) and item.strip():
            out.append({"ts_code": item.strip()})
            continue
    return out


class SearchHit(BaseModel):
    canonical_id: str
    score: float
    title: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    event_type: str | None = None
    tickers: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("tickers", mode="before")
    @classmethod
    def _v_tickers(cls, v: Any) -> list[dict[str, Any]]:
        return _normalize_tickers(v)


@app.get("/health", operation_id="health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def ui_index() -> Response:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>TX-News</h1><p>UI not found.</p>")
    return FileResponse(str(index))


@app.get("/dashboard", include_in_schema=False)
@app.get("/dashboard/", include_in_schema=False)
def ui_dashboard() -> Response:
    return ui_index()


@app.get("/config", include_in_schema=False)
@app.get("/config/", include_in_schema=False)
def ui_config() -> Response:
    return ui_index()


@app.get("/status", operation_id="status")
def status(llm: bool = Query(False)) -> Response:
    """
    Public-facing status for the chat UI (keep minimal; do not expose admin UI).
    """
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    deps: dict[str, Any] = {}
    try:
        with engine.connect() as conn:
            counts = {
                "articles": int(conn.execute(text("select count(*) from articles")).scalar() or 0),
                "analyses": int(conn.execute(text("select count(*) from analyses")).scalar() or 0),
                "raw_documents": int(conn.execute(text("select count(*) from raw_documents")).scalar() or 0),
                "signals": int(conn.execute(text("select count(*) from signals")).scalar() or 0),
                "a_share_basic": int(conn.execute(text("select count(*) from a_share_basic")).scalar() or 0),
            }
        deps["postgres"] = {"ok": True}
    except Exception as e:
        counts = {}
        deps["postgres"] = {"ok": False, "error": str(e)}

    try:
        q = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection)
        q._client().get_collections()  # type: ignore[attr-defined]
        deps["qdrant"] = {"ok": True}
    except Exception as e:
        deps["qdrant"] = {"ok": False, "error": str(e)}

    if llm:
        llm_cfg = settings.resolve_llm_chat(file_cfg)
        base_url = str(llm_cfg.get("base_url") or "").strip().rstrip("/")
        model = str(llm_cfg.get("model") or "").strip()
        api_key = str(llm_cfg.get("api_key") or "").strip()
        host = (urlparse(base_url).hostname or "").strip() if base_url else ""
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
        api_key_masked = (api_key[:4] + "…" + api_key[-4:]) if len(api_key) >= 10 else ("***" if api_key else "")

        info: dict[str, Any] = {
            "reachable": False,
            "auth_ok": False,
            "ok": False,
            "base_url": base_url,
            "host": host,
            "model": model,
            "api_key_set": bool(api_key),
            "api_key_masked": api_key_masked,
            "require_user_llm": bool(settings.require_user_llm),
            "proxy_set": bool(proxy),
            "no_proxy_set": bool(no_proxy),
        }
        if not base_url:
            info["error"] = "missing_base_url"
        elif not api_key and not settings.require_user_llm:
            info["error"] = "missing_api_key"
        else:
            try:
                # Prefer /models to avoid spending tokens; still validates DNS/TLS/HTTP path.
                url = f"{base_url}/models"
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=20.0)
                with httpx.Client(timeout=timeout, trust_env=True) as client:
                    r = client.get(url, headers=headers)
                info["reachable"] = True
                info["http_status"] = int(r.status_code)
                if r.status_code in {401, 403}:
                    info["error"] = "unauthorized"
                else:
                    # 200: /models supported; 404: some providers may not implement it but are reachable.
                    info["auth_ok"] = True
                    info["ok"] = True
                    if r.status_code == 404:
                        info["note"] = "models_endpoint_404"
            except Exception as e:
                info["error"] = str(e)
        deps["llm_chat"] = info

    return JSONResponse({"dependencies": deps, "counts": counts}, headers=NO_STORE_HEADERS)


def _redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url)


def _ensure_uid(request: Request) -> tuple[str, dict[str, str]]:
    uid = (request.cookies.get(UID_COOKIE) or "").strip()
    if uid:
        return uid, {}
    uid = make_user_id()
    return uid, {
        "Set-Cookie": f"{UID_COOKIE}={uid}; Path=/; HttpOnly; SameSite=Lax",
    }


class UserLLMConfigIn(BaseModel):
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str = Field(min_length=1)


class FeedbackIn(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)


@app.post("/feedback", operation_id="create_feedback")
def create_feedback(request: Request, body: FeedbackIn) -> Response:
    """
    Lightweight UI feedback/log endpoint for closing the loop (clicks/likes/graph interactions).
    Uses the per-user cookie uid to support per-user evaluation without requiring auth.
    """
    uid, headers = _ensure_uid(request)
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    data = dict(body.data or {})
    # Best-effort request metadata (avoid storing raw headers).
    data.setdefault("path", str(request.url.path))
    data.setdefault("client", getattr(getattr(request, "client", None), "host", None))
    data.setdefault("ua", str(request.headers.get("user-agent") or "")[:300])
    insert_feedback(engine, uid=uid, kind=body.kind, data=data)
    return JSONResponse({"ok": True}, headers={**headers, **NO_STORE_HEADERS})


@app.get("/api/config", operation_id="get_user_llm_config")
def api_get_config(request: Request) -> Response:
    uid, headers = _ensure_uid(request)
    cfg = get_user_llm_config(_redis(), uid)
    out = {"uid": uid, "configured": bool(cfg), "config": (cfg.masked() if cfg else None)}
    return JSONResponse(content=out, headers={**headers, **NO_STORE_HEADERS})


@app.post("/api/config", operation_id="set_user_llm_config")
def api_set_config(request: Request, body: UserLLMConfigIn) -> Response:
    uid, headers = _ensure_uid(request)
    settings = get_settings()
    ttl = int(getattr(settings, "user_llm_ttl_seconds", 0) or 0)
    set_user_llm_config(
        _redis(),
        uid=uid,
        base_url=body.base_url,
        model=body.model,
        api_key=body.api_key,
        ttl_seconds=ttl if ttl > 0 else None,
    )
    cfg = get_user_llm_config(_redis(), uid)
    out = {"uid": uid, "configured": bool(cfg), "config": (cfg.masked() if cfg else None)}
    return JSONResponse(content=out, headers={**headers, **NO_STORE_HEADERS})


@app.post("/api/config/clear", operation_id="clear_user_llm_config")
def api_clear_config(request: Request) -> Response:
    uid, headers = _ensure_uid(request)
    clear_user_llm_config(_redis(), uid)
    out = {"uid": uid, "configured": False, "config": None}
    return JSONResponse(content=out, headers={**headers, **NO_STORE_HEADERS})


@app.get("/dashboard/summary", operation_id="dashboard_summary")
def dashboard_summary(
    minutes: int = Query(default=180, ge=5, le=60 * 24),
    limit: int = Query(default=40, ge=1, le=200),
) -> Response:
    """
    Public summary used by the dashboard page (no admin/ops data, only aggregated DB results).
    """
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    window_seconds = int(minutes) * 60
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select kind, count(*) as n "
                "from signals "
                "where created_at >= (now() - (:sec || ' seconds')::interval) "
                "group by kind"
            ),
            {"sec": window_seconds},
        ).all()
        signals_by_kind = {str(r[0]): int(r[1] or 0) for r in rows}

        rows = conn.execute(
            text(
                "select coalesce(event_type, 'other') as event_type, count(*) as n "
                "from analyses "
                "where created_at >= (now() - (:sec || ' seconds')::interval) "
                "group by event_type "
                "order by n desc "
                "limit 12"
            ),
            {"sec": window_seconds},
        ).all()
        top_event_types = [{"event_type": str(r[0] or "other"), "count": int(r[1] or 0)} for r in rows]

        rows = conn.execute(
            text(
                "select canonical_id, kind, created_at "
                "from signals "
                "where created_at >= (now() - (:sec || ' seconds')::interval) "
                "order by created_at desc "
                "limit :limit"
            ),
            {"sec": window_seconds, "limit": int(limit)},
        ).all()

    recent: list[dict[str, Any]] = []
    tickers_count: dict[str, int] = {}
    for canonical_id, kind, created_at in rows:
        cid = str(canonical_id)
        a = get_article(engine, cid)
        v = get_latest_version(engine, cid)
        an = get_analysis(engine, cid)
        data = an.data if an and isinstance(an.data, dict) else {}
        tickers = _normalize_tickers(data.get("tickers"))
        for t in tickers:
            ts = str((t or {}).get("ts_code") or "").strip()
            if ts:
                tickers_count[ts] = tickers_count.get(ts, 0) + 1
        recent.append(
            {
                "canonical_id": cid,
                "kind": str(kind),
                "created_at": created_at.isoformat() if created_at else None,
                "title": getattr(a, "title", None) if a else None,
                "url": v.url if v else None,
                "published_at": v.published_at.isoformat() if v and v.published_at else None,
                "event_type": str(getattr(an, "event_type", None) or "") or None,
                "tickers": tickers,
                "impact": data.get("impact"),
                "llm_used": bool(getattr(an, "llm_used", 0)) if an else False,
                "deep_optimized_at": data.get("deep_optimized_at"),
            }
        )

    top_tickers = [{"ts_code": k, "count": v} for k, v in sorted(tickers_count.items(), key=lambda x: x[1], reverse=True)[:20]]
    return JSONResponse(
        {
            "window_minutes": int(minutes),
            "signals_by_kind": signals_by_kind,
            "top_event_types": top_event_types,
            "top_tickers": top_tickers,
            "recent": recent,
        },
        headers=NO_STORE_HEADERS,
    )


@app.get("/kg/graph", operation_id="kg_graph")
def kg_graph(
    minutes: int = Query(default=180, ge=5, le=60 * 24),
    limit_articles: int = Query(default=600, ge=50, le=5000),
    max_events: int = Query(default=80, ge=10, le=500),
    max_tickers: int = Query(default=160, ge=10, le=2000),
) -> Response:
    """
    Public near-real-time KG graph snapshot used by the dashboard visualization.
    Current node types are fixed: event (event_id) and ticker (ts_code).
    """
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    window_seconds = int(minutes) * 60
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "with recent as ("
                "  select canonical_id, event_type, data->>'event_id' as event_id, data->'tickers' as tickers, created_at "
                "  from analyses "
                "  where created_at >= (now() - (:sec || ' seconds')::interval) "
                "  and (data->>'event_id') is not null and (data->>'event_id') <> ''"
                "), latest_v as ("
                "  select distinct on (canonical_id) canonical_id, url, source_id, "
                "    coalesce(published_at, fetched_at) as published_at "
                "  from article_versions "
                "  order by canonical_id, coalesce(published_at, fetched_at) desc"
                ") "
                "select r.canonical_id, r.event_id, r.event_type, r.tickers, r.created_at, "
                "  a.title, lv.url, lv.published_at, lv.source_id "
                "from recent r "
                "left join articles a on a.canonical_id = r.canonical_id "
                "left join latest_v lv on lv.canonical_id = r.canonical_id "
                "order by r.created_at desc "
                "limit :limit"
            ),
            {"sec": window_seconds, "limit": int(limit_articles)},
        ).all()

    canonical_ids = {str(r[0]) for r in rows if r[0]}
    feedback_by_canonical = get_feedback_counts(
        engine,
        canonical_ids=sorted(canonical_ids),
        kinds=["kg_graph_article_thumb_up", "kg_graph_article_thumb_down"],
        window_seconds=window_seconds,
    )

    def _feedback_for(canonical_id: str) -> tuple[int, int]:
        entry = feedback_by_canonical.get(str(canonical_id), {})
        up = int(entry.get("kg_graph_article_thumb_up") or 0)
        down = int(entry.get("kg_graph_article_thumb_down") or 0)
        return up, down

    def _feedback_payload(up: int | None, down: int | None) -> dict[str, int]:
        up = int(up or 0)
        down = int(down or 0)
        return {"up": up, "down": down, "score": up - down}

    # Build event/ticker bipartite graph from analyses.
    event_by_id: dict[str, dict[str, Any]] = {}
    ticker_count: dict[str, int] = {}
    edge_count: dict[tuple[str, str], dict[str, Any]] = {}

    def _tickers(v: Any) -> list[dict[str, Any]]:
        return _normalize_tickers(v)

    for canonical_id, event_id, event_type, tickers, created_at, title, url, published_at, source_id in rows:
        cid = str(canonical_id or "").strip()
        if not cid:
            continue
        eid = str(event_id or "").strip()
        if not eid:
            continue
        fb_up, fb_down = _feedback_for(cid)
        ev = event_by_id.setdefault(
            eid,
            {
                "event_id": eid,
                "event_type": str(event_type or "other"),
                "articles": [],
                "count": 0,
                "feedback_up": 0,
                "feedback_down": 0,
                "last_created_at": None,
            },
        )
        ev["count"] = int(ev.get("count") or 0) + 1
        ev["feedback_up"] = int(ev.get("feedback_up") or 0) + fb_up
        ev["feedback_down"] = int(ev.get("feedback_down") or 0) + fb_down
        ts = created_at.isoformat() if created_at else None
        if ts and (not ev["last_created_at"] or ts > ev["last_created_at"]):
            ev["last_created_at"] = ts
        if len(ev["articles"]) < 12:
            ev["articles"].append(
                {
                    "canonical_id": cid,
                    "title": title,
                    "url": url,
                    "source_id": source_id,
                    "published_at": published_at.isoformat() if published_at else None,
                    "created_at": ts,
                    "thumbs_up": fb_up,
                    "thumbs_down": fb_down,
                }
            )

        for t in _tickers(tickers):
            ts_code = str((t or {}).get("ts_code") or "").strip()
            if not ts_code:
                continue
            ticker_count[ts_code] = ticker_count.get(ts_code, 0) + 1
            key = (eid, ts_code)
            e = edge_count.setdefault(
                key,
                {
                    "event_id": eid,
                    "ts_code": ts_code,
                    "count": 0,
                    "evidence": [],
                    "feedback_up": 0,
                    "feedback_down": 0,
                },
            )
            e["count"] = int(e.get("count") or 0) + 1
            e["feedback_up"] = int(e.get("feedback_up") or 0) + fb_up
            e["feedback_down"] = int(e.get("feedback_down") or 0) + fb_down
            if len(e["evidence"]) < 12:
                e["evidence"].append(cid)

    # Trim to keep the dashboard visualization responsive.
    def _event_rank(item: dict[str, Any]) -> int:
        base = int(item.get("count") or 0)
        score = int(item.get("feedback_up") or 0) - int(item.get("feedback_down") or 0)
        return base + score

    top_event_ids = [
        k
        for k, _ in sorted(
            event_by_id.items(),
            key=lambda kv: _event_rank(kv[1]),
            reverse=True,
        )[: int(max_events)]
    ]
    top_events = {eid: event_by_id[eid] for eid in top_event_ids if eid in event_by_id}

    top_ts_codes = [k for k, _ in sorted(ticker_count.items(), key=lambda kv: kv[1], reverse=True)[: int(max_tickers)]]
    a_share_rows = get_a_shares(engine, top_ts_codes)
    ticker_name = {r.ts_code: r.name for r in a_share_rows}

    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []

    for eid, ev in top_events.items():
        feedback = _feedback_payload(ev.get("feedback_up"), ev.get("feedback_down"))
        count_raw = int(ev.get("count") or 0)
        count = max(1, count_raw + feedback["score"])
        nodes.append(
            {
                "id": f"event:{eid}",
                "kind": "event",
                "label": str(ev.get("event_type") or eid),
                "event_id": eid,
                "event_type": ev.get("event_type"),
                "count": count,
                "count_raw": count_raw,
                "feedback": feedback,
                "last_created_at": ev.get("last_created_at"),
                "articles": ev.get("articles") or [],
            }
        )

    allowed_events = set(top_events.keys())
    allowed_tickers = set(top_ts_codes)

    for ts_code in top_ts_codes:
        nodes.append(
            {
                "id": f"ticker:{ts_code}",
                "kind": "ticker",
                "label": f"{ts_code} {ticker_name.get(ts_code) or ''}".strip(),
                "ts_code": ts_code,
                "name": ticker_name.get(ts_code),
                "count": int(ticker_count.get(ts_code) or 0),
            }
        )

    for (eid, ts_code), e in edge_count.items():
        if eid not in allowed_events or ts_code not in allowed_tickers:
            continue
        feedback = _feedback_payload(e.get("feedback_up"), e.get("feedback_down"))
        weight_raw = int(e.get("count") or 0)
        weight = max(1, weight_raw + feedback["score"])
        links.append(
            {
                "source": f"event:{eid}",
                "target": f"ticker:{ts_code}",
                "relation": "mentions",
                "weight": weight,
                "weight_raw": weight_raw,
                "feedback": feedback,
                "evidence": e.get("evidence") or [],
            }
        )

    return JSONResponse(
        {
            "window_minutes": int(minutes),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "nodes": nodes,
            "links": links,
            "stats": {
                "events": len(top_events),
                "tickers": len(top_ts_codes),
                "links": len(links),
            },
        },
        headers=NO_STORE_HEADERS,
    )


@app.get("/search", response_model=list[SearchHit], operation_id="search_news")
def search(q: str = Query(min_length=1), limit: int = Query(default=10, ge=1, le=50)):
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
    embedder, qdrant_strategy = build_embedder(embedding_cfg)
    model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
    vector = embedder.embed(q[:2000])

    qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection).resolve_collection_for_embedding(
        vector_size=len(vector),
        model_name_or_path=model_name,
        strategy=qdrant_strategy,
    )
    points = qdrant.search(vector=vector, limit=limit)

    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    hits: list[SearchHit] = []
    for p in points:
        cid = scored_point_canonical_id(p) or str(p.id)
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
                tickers=_normalize_tickers((an.data.get("tickers") if an else None)),
            )
        )
    return hits


@app.get("/articles/{canonical_id}", operation_id="get_article_analysis")
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


@app.get("/kb/articles/{canonical_id}", operation_id="kb_get_article_full")
def kb_article_full(canonical_id: str) -> Response:
    """
    Internal KB API: return full extracted text (requires TXNEWS_ALLOW_FULL_TEXT=1).
    This endpoint is intentionally separated from /articles to keep default behavior compliant.
    """
    settings = get_settings()
    if not bool(getattr(settings, "allow_full_text", False)):
        raise HTTPException(status_code=403, detail="full_text_disabled")
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    a = get_article(engine, canonical_id)
    if not a:
        return {"error": "not_found"}
    v = get_latest_version(engine, canonical_id)
    an = get_analysis(engine, canonical_id)
    return JSONResponse(
        {
            "canonical_id": canonical_id,
            "title": a.title,
            "latest_url": v.url if v else None,
            "published_at": v.published_at.isoformat() if v and v.published_at else None,
            "text": a.text,
            "analysis": an.data if an else None,
        },
        headers=NO_STORE_HEADERS,
    )


@app.get("/kb/search", operation_id="kb_search_full")
def kb_search(q: str = Query(min_length=1), limit: int = Query(default=10, ge=1, le=50)) -> Response:
    """
    Internal KB API: vector search + return full extracted text for each hit.
    Requires TXNEWS_ALLOW_FULL_TEXT=1.
    """
    settings = get_settings()
    if not bool(getattr(settings, "allow_full_text", False)):
        raise HTTPException(status_code=403, detail="full_text_disabled")

    file_cfg = settings.load_file_settings()
    embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
    embedder, qdrant_strategy = build_embedder(embedding_cfg)
    model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
    vector = embedder.embed(q[:2000])

    qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection).resolve_collection_for_embedding(
        vector_size=len(vector),
        model_name_or_path=model_name,
        strategy=qdrant_strategy,
    )
    points = qdrant.search(vector=vector, limit=limit)

    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    out: list[dict[str, Any]] = []
    for p in points:
        cid = scored_point_canonical_id(p) or str(p.id)
        a = get_article(engine, cid)
        v = get_latest_version(engine, cid)
        an = get_analysis(engine, cid)
        if not a:
            continue
        out.append(
            {
                "canonical_id": cid,
                "score": float(p.score or 0.0),
                "title": a.title,
                "url": v.url if v else None,
                "published_at": v.published_at.isoformat() if v and v.published_at else None,
                "event_type": an.event_type if an else None,
                "tickers": _normalize_tickers((an.data.get("tickers") if an else None)),
                "text": a.text,
            }
        )
    return JSONResponse(out, headers=NO_STORE_HEADERS)


@app.get("/signals", operation_id="list_signals")
def signals(limit: int = Query(default=50, ge=1, le=200)) -> Response:
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                with latest as (
                  select distinct on (canonical_id)
                    canonical_id, url, published_at, fetched_at
                  from article_versions
                  order by canonical_id, published_at desc nulls last, fetched_at desc
                )
                select
                  s.canonical_id,
                  s.kind,
                  s.data as signal_data,
                  s.created_at,
                  a.title,
                  an.event_type,
                  an.data as analysis_data,
                  an.llm_used as llm_used,
                  l.url,
                  l.published_at
                from signals s
                left join articles a on a.canonical_id = s.canonical_id
                left join analyses an on an.canonical_id = s.canonical_id
                left join latest l on l.canonical_id = s.canonical_id
                order by s.created_at desc
                limit :limit
                """
            ),
            {"limit": int(limit)},
        ).all()

    out: list[dict[str, Any]] = []
    for canonical_id, kind, signal_data, created_at, title, event_type, analysis_data, llm_used, url, published_at in rows:
        data = analysis_data if isinstance(analysis_data, dict) else {}
        tickers = _normalize_tickers(data.get("tickers"))
        impact = data.get("impact") if isinstance(data.get("impact"), dict) else None
        deep_optimized_at = data.get("deep_optimized_at") if isinstance(data.get("deep_optimized_at"), str) else None

        ts_codes = []
        for t in tickers:
            ts = str((t or {}).get("ts_code") or "").strip()
            if ts:
                ts_codes.append(ts)
        ts_codes = ts_codes[:4]

        kind_s = str(kind)
        event_type_s = str(event_type or "").strip() or None
        if kind_s == "analysis_updated":
            summary = f"分析完成：{event_type_s or 'other'}"
            if ts_codes:
                summary += f" · {', '.join(ts_codes)}"
        elif kind_s == "deep_analysis_updated":
            summary = f"深分析完成：{event_type_s or 'other'}"
            if ts_codes:
                summary += f" · {', '.join(ts_codes)}"
        elif kind_s == "breaking":
            reason = None
            if isinstance(signal_data, dict):
                reason = signal_data.get("reason")
            summary = "发现新文章" if reason == "new_canonical" else "Breaking"
        else:
            summary = kind_s

        out.append(
            {
                "canonical_id": str(canonical_id),
                "kind": kind_s,
                "created_at": created_at.isoformat() if created_at else None,
                "title": title,
                "url": url,
                "published_at": published_at.isoformat() if published_at else None,
                "event_type": event_type_s,
                "tickers": tickers,
                "impact": impact,
                "llm_used": bool(llm_used or 0),
                "deep_optimized_at": deep_optimized_at,
                "summary": summary,
            }
        )

    return JSONResponse(out, headers=NO_STORE_HEADERS)


@app.get("/events/{event_id}", operation_id="get_event_timeline")
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


@app.get("/entities/{ts_code}", operation_id="get_entity_profile")
def entity_profile(ts_code: str) -> dict[str, Any]:
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    row = get_a_share(engine, ts_code)
    if not row:
        bootstrap = bootstrap_a_share_basic_from_cache(engine)
        row = get_a_share(engine, ts_code)
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


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    max_steps: int = 50
    recent_minutes: int = 180


class ChatResponse(BaseModel):
    message: dict[str, Any]


def _resolve_user_llm(request: Request) -> dict[str, Any] | None:
    """
    Resolve per-user LLM config from Redis if cookie exists.
    Returns None when not configured.
    """
    settings = get_settings()
    uid = (request.cookies.get(UID_COOKIE) or "").strip()
    if not uid:
        return None
    try:
        r = Redis.from_url(settings.redis_url)
        cfg = get_user_llm_config(r, uid)
    except Exception:
        return None
    if not cfg:
        return None
    return {"api_key": cfg.api_key, "base_url": cfg.base_url, "model": cfg.model}


def _sse(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

def _sse_done(message: dict[str, Any]) -> str:
    """
    Standardize the `done` SSE payload shape for the frontend.
    The UI expects: event=done, data={"message": {...}}.
    """
    return _sse("done", {"message": message})

def _config_url(request: Request) -> str:
    # Prefer the public same-origin config page (works behind reverse proxies / tunnels).
    base = str(getattr(request, "base_url", "") or "").rstrip("/")
    if base:
        return f"{base}/config"
    return "/config"

def _is_local_llm_base_url(base_url: str) -> bool:
    s = (base_url or "").strip().lower()
    if not s:
        return False
    if "host.docker.internal" in s:
        return True
    if "localhost" in s or "127.0.0.1" in s:
        return True
    if "://vllm" in s:
        return True
    return False


def _has_network_error(e: BaseException) -> bool:
    cur: BaseException | None = e
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (httpx.TimeoutException, httpx.TransportError)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _iter_exception_chain(e: BaseException) -> list[BaseException]:
    cur: BaseException | None = e
    seen: set[int] = set()
    out: list[BaseException] = []
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def _extract_llm_error(e: BaseException) -> dict[str, Any] | None:
    for cur in _iter_exception_chain(e):
        if isinstance(cur, httpx.HTTPStatusError) and getattr(cur, "response", None) is not None:
            resp = cur.response
            status = int(getattr(resp, "status_code", 0) or 0)
            payload: Any = None
            try:
                payload = resp.json()
            except Exception:
                payload = None
            message = ""
            err_type = ""
            code = ""
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    message = str(err.get("message") or "")
                    err_type = str(err.get("type") or "")
                    code = str(err.get("code") or "")
                elif isinstance(err, str):
                    message = str(err)
            if not message:
                text = str(getattr(resp, "text", "") or "").strip()
                message = text
            message = " ".join(message.split())
            if len(message) > 300:
                message = message[:300] + "…"
            return {"status": status, "message": message, "type": err_type, "code": code}
    return None


def _is_provider_blocked_error(info: dict[str, Any]) -> bool:
    status = int(info.get("status") or 0)
    if status in {401, 402, 403}:
        return True
    msg = str(info.get("message") or "").lower()
    err_type = str(info.get("type") or "").lower()
    code = str(info.get("code") or "").lower()
    tokens = ["arrearage", "overdue", "insufficient", "billing", "payment", "quota", "balance", "credit", "access denied"]
    if any(t in msg for t in tokens):
        return True
    if err_type in {"arrearage", "insufficient_balance"}:
        return True
    if code in {"arrearage", "insufficient_balance"}:
        return True
    return False


def _should_fallback_to_deep(err_info: dict[str, Any] | None, e: BaseException) -> bool:
    if _has_network_error(e):
        return True
    if err_info and _is_provider_blocked_error(err_info):
        return True
    return False


def _format_llm_error(err_info: dict[str, Any] | None, e: BaseException) -> str:
    if err_info:
        detail = err_info.get("message") or ""
        kind = err_info.get("type") or err_info.get("code") or ""
        status = err_info.get("status") or ""
        parts = [p for p in [str(kind).strip(), str(detail).strip()] if p]
        suffix = f"({status}) " if status else ""
        return f"{suffix}{' / '.join(parts) or 'HTTP error'}".strip()
    return str(e)


def _resolve_deep_llm_for_chat(settings: Any, file_cfg: Any, accel: str) -> dict[str, Any]:
    deep_llm = settings.resolve_llm_deep(file_cfg) if accel == "gpu" else {}
    if accel != "cpu":
        return deep_llm
    llm = file_cfg.llm or {}
    deep_cfg = llm.get("deep") if isinstance(llm.get("deep"), dict) else {}
    deep_env_is_set = any(
        [
            (settings.llm_deep_base_url or "").strip(),
            (settings.llm_deep_model_name or "").strip(),
            (settings.llm_deep_api_key or "").strip(),
            settings.llm_deep_timeout_seconds is not None,
        ]
    )
    if not deep_cfg and not deep_env_is_set:
        return deep_llm
    base_url = (
        (settings.llm_deep_base_url or "").strip()
        or str(deep_cfg.get("base_url") or "").strip()
        or "http://127.0.0.1:9999/v1"
    )
    model = (
        (settings.llm_deep_model_name or "").strip()
        or str(deep_cfg.get("model") or "").strip()
        or "deepseekr1-merged"
    )
    api_key = (
        (settings.llm_deep_api_key or "").strip()
        or str(deep_cfg.get("api_key") or "").strip()
        or None
    )
    timeout_seconds = int(settings.llm_deep_timeout_seconds or deep_cfg.get("timeout_seconds") or 120)
    provider = str(deep_cfg.get("provider") or "openai_compat")
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "timeout_seconds": timeout_seconds,
    }


@app.post("/chat", response_model=ChatResponse, operation_id="chat_agent")
def chat_agent(req: ChatRequest, request: Request) -> ChatResponse:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    llm = settings.resolve_llm_chat(file_cfg)
    user_llm = _resolve_user_llm(request) or {}
    api_key = user_llm.get("api_key") or llm.get("api_key")
    model = user_llm.get("model") or llm.get("model") or "qwen3-max"
    base_url = user_llm.get("base_url") or llm.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_seconds = int(llm.get("timeout_seconds") or 60)

    accel = (settings.accelerator or "").strip().lower() or "cpu"
    deep_llm = _resolve_deep_llm_for_chat(settings, file_cfg, accel)
    deep_base_url = str(deep_llm.get("base_url") or "").strip()
    deep_model = str(deep_llm.get("model") or "deepseekr1-merged").strip()
    deep_timeout = int(deep_llm.get("timeout_seconds") or 120)
    can_fallback_to_deep = bool(
        settings.chat_allow_deep_fallback
        and (not settings.require_user_llm)
        and deep_base_url
        and deep_base_url != str(base_url)
        and _is_local_llm_base_url(deep_base_url)
    )

    if not api_key:
        if settings.require_user_llm:
            return ChatResponse(
                message={
                    "role": "assistant",
                    "content": f"未配置个人 LLM：请先访问 {_config_url(request)} 设置 base_url/model/api_key。",
                    "meta": {"tools": [], "evidence": []},
                }
            )
        if can_fallback_to_deep:
            api_key = ""
            model = deep_model
            base_url = deep_base_url
            timeout_seconds = deep_timeout
        else:
            return ChatResponse(
                message={
                    "role": "assistant",
                    "content": (
                        "未配置 LLM API Key：请在环境变量 `TXNEWS_LLM_API_KEY`（或兼容的 `DASHSCOPE_API_KEY` / `OPENAI_API_KEY`），"
                        "或 `config/config.yaml` 的 `llm.chat.api_key`（兼容 `llm.api_key`）中配置后再试。"
                    ),
                    "meta": {"tools": [], "evidence": []},
                }
            )

    try:
        agent = TxNewsAgent(
            api_key=str(api_key or ""),
            model=str(model),
            base_url=str(base_url),
            timeout_seconds=timeout_seconds,
        )
        out = agent.run(
            messages=[m.model_dump() for m in req.messages],
            max_steps=req.max_steps,
            recent_minutes=req.recent_minutes,
        )
        return ChatResponse(message=out)
    except AgentChatError as e:
        return ChatResponse(
            message={
                "role": "assistant",
                "content": f"对话失败：{e}",
                "meta": {"tools": [], "evidence": []},
            }
        )
    except Exception as e:
        err_info = _extract_llm_error(e)
        if can_fallback_to_deep and _should_fallback_to_deep(err_info, e):
            try:
                agent = TxNewsAgent(
                    api_key=str(deep_llm.get("api_key") or ""),
                    model=deep_model,
                    base_url=deep_base_url,
                    timeout_seconds=deep_timeout,
                )
                out = agent.run(
                    messages=[m.model_dump() for m in req.messages],
                    max_steps=req.max_steps,
                    recent_minutes=req.recent_minutes,
                )
                return ChatResponse(message=out)
            except Exception:
                pass
        hint = ""
        if err_info and _is_provider_blocked_error(err_info):
            hint = "提示：在线 LLM 返回鉴权/计费错误（常见为欠费或 Key 无权限）。请更换/充值，或改用本地 vLLM。\n"
        return ChatResponse(
            message={
                "role": "assistant",
                "content": (
                    "对话失败：LLM/检索/向量化组件不可用。\n"
                    f"错误：{_format_llm_error(err_info, e)}\n"
                    f"{hint}"
                    "请检查：容器内是否能访问 `llm.chat.base_url`（网络/代理/证书/超时），以及 LLM 的 base_url/api_key 配置；"
                    "并确认 Qdrant 正常运行与 embedding 模型可用。"
                ),
                "meta": {"tools": [], "evidence": []},
            }
        )


@app.post("/chat/stream", operation_id="chat_agent_stream")
def chat_agent_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    llm = settings.resolve_llm_chat(file_cfg)
    user_llm = _resolve_user_llm(request) or {}
    api_key = user_llm.get("api_key") or llm.get("api_key")
    model = user_llm.get("model") or llm.get("model") or "qwen3-max"
    base_url = user_llm.get("base_url") or llm.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_seconds = int(llm.get("timeout_seconds") or 60)
    trace_id = secrets.token_hex(4)

    accel = (settings.accelerator or "").strip().lower() or "cpu"
    deep_llm = _resolve_deep_llm_for_chat(settings, file_cfg, accel)
    deep_base_url = str(deep_llm.get("base_url") or "").strip()
    deep_model = str(deep_llm.get("model") or "deepseekr1-merged").strip()
    deep_timeout = int(deep_llm.get("timeout_seconds") or 120)
    can_fallback_to_deep = bool(
        settings.chat_allow_deep_fallback
        and (not settings.require_user_llm)
        and deep_base_url
        and deep_base_url != str(base_url)
        and _is_local_llm_base_url(deep_base_url)
    )

    def gen():
        started = time.time()
        user_chars = sum(len(m.content or "") for m in req.messages if m.role == "user")
        logger.info(
            "chat_stream start trace=%s model=%s base_url=%s timeout=%ss user_chars=%s msgs=%s",
            trace_id,
            model,
            base_url,
            timeout_seconds,
            user_chars,
            len(req.messages),
        )
        yield _sse("ready", {"ok": True})
        if not api_key:
            logger.warning("chat_stream no api key trace=%s", trace_id)
            if settings.require_user_llm:
                yield _sse_done(
                    {
                        "role": "assistant",
                        "content": f"未配置个人 LLM：请先访问 {_config_url(request)} 设置 base_url/model/api_key。",
                        "meta": {"tools": [], "evidence": []},
                    }
                )
                return
            if can_fallback_to_deep:
                # Explicit opt-in: allow local vLLM for chat in GPU mode when no cloud key is configured.
                api_key_local = ""
                model_local = deep_model
                base_url_local = deep_base_url
                timeout_local = deep_timeout
            else:
                yield _sse_done(
                    {
                        "role": "assistant",
                        "content": (
                            "未配置 LLM API Key：请在环境变量 `TXNEWS_LLM_API_KEY`（或兼容的 `DASHSCOPE_API_KEY` / `OPENAI_API_KEY`），"
                            "或 `config/config.yaml` 的 `llm.chat.api_key`（兼容 `llm.api_key`）中配置后再试。"
                        ),
                        "meta": {"tools": [], "evidence": []},
                    }
                )
                return
        else:
            api_key_local = str(api_key)
            model_local = str(model)
            base_url_local = str(base_url)
            timeout_local = int(timeout_seconds)

        try:
            def _run_stream(*, agent: TxNewsAgent) -> None:
                nonlocal first_delta_at, delta_chars
                for ev in agent.run_stream(
                    messages=[m.model_dump() for m in req.messages],
                    max_steps=req.max_steps,
                    recent_minutes=req.recent_minutes,
                ):
                    t = ev.get("type")
                    if t == "delta":
                        chunk = ev.get("content") or ""
                        if chunk:
                            delta_chars += len(chunk)
                            if first_delta_at is None:
                                first_delta_at = time.time()
                                logger.info(
                                    "chat_stream first_delta trace=%s ttfb=%.2fs",
                                    trace_id,
                                    first_delta_at - started,
                                )
                            if logger.isEnabledFor(logging.DEBUG) and delta_chars % 400 < len(chunk):
                                logger.debug("chat_stream progress trace=%s chars=%s", trace_id, delta_chars)
                        yield _sse("delta", {"content": chunk})
                    elif t == "tool_call":
                        logger.info(
                            "chat_stream tool_call trace=%s name=%s arguments=%s",
                            trace_id,
                            ev.get("name"),
                            ev.get("arguments"),
                        )
                        yield _sse("tool", {"name": ev.get("name"), "arguments": ev.get("arguments")})
                    elif t == "tool_result":
                        logger.info(
                            "chat_stream tool_result trace=%s name=%s ok=%s duration_ms=%s summary=%s",
                            trace_id,
                            ev.get("name"),
                            ev.get("ok"),
                            ev.get("duration_ms"),
                            ev.get("summary"),
                        )
                        yield _sse(
                            "tool_result",
                            {
                                "name": ev.get("name"),
                                "ok": ev.get("ok"),
                                "duration_ms": ev.get("duration_ms"),
                                "summary": ev.get("summary"),
                            },
                        )
                    elif t == "done":
                        done_at = time.time()
                        msg = ev.get("message") or {}
                        meta = msg.get("meta") if isinstance(msg, dict) else None
                        tools_n = len((meta or {}).get("tools") or []) if isinstance(meta, dict) else 0
                        ev_n = len((meta or {}).get("evidence") or []) if isinstance(meta, dict) else 0
                        logger.info(
                            "chat_stream done trace=%s elapsed=%.2fs delta_chars=%s tools=%s evidence=%s",
                            trace_id,
                            done_at - started,
                            delta_chars,
                            tools_n,
                            ev_n,
                        )
                        yield _sse_done(ev.get("message") or {})
                        return
                logger.warning("chat_stream ended without done trace=%s", trace_id)
                yield _sse_done({"role": "assistant", "content": "对话失败：无返回", "meta": {"tools": [], "evidence": []}})

            first_delta_at: float | None = None
            delta_chars = 0

            primary = TxNewsAgent(
                api_key=str(api_key_local or ""),
                model=str(model_local),
                base_url=str(base_url_local),
                timeout_seconds=int(timeout_local),
            )

            # Try primary first; on network/auth/billing errors, optionally fall back to local vLLM.
            try:
                yield from _run_stream(agent=primary)
                return
            except Exception as e:
                err_info = _extract_llm_error(e)
                if can_fallback_to_deep and _should_fallback_to_deep(err_info, e):
                    logger.warning(
                        "chat_stream primary llm failed; fallback to deep llm trace=%s err=%s",
                        trace_id,
                        e,
                    )
                    note = "（在线 LLM 不可用，已切换到本地 vLLM）"
                    if _has_network_error(e):
                        note = "（网络不稳定，已切换到本地 vLLM）"
                    yield _sse("delta", {"content": f"\n\n{note}\n"})
                    fallback = TxNewsAgent(
                        api_key=str(deep_llm.get("api_key") or ""),
                        model=deep_model,
                        base_url=deep_base_url,
                        timeout_seconds=deep_timeout,
                    )
                    yield from _run_stream(agent=fallback)
                    return
                raise
        except AgentChatError as e:
            logger.warning("chat_stream agent error trace=%s err=%s", trace_id, e)
            yield _sse_done({"role": "assistant", "content": f"对话失败：{e}", "meta": {"tools": [], "evidence": []}})
        except Exception as e:
            logger.exception("chat_stream exception trace=%s", trace_id)
            err_info = _extract_llm_error(e)
            hint = ""
            if err_info and _is_provider_blocked_error(err_info):
                hint = "提示：在线 LLM 返回鉴权/计费错误（常见为欠费或 Key 无权限）。请更换/充值，或改用本地 vLLM。\n"
            yield _sse_done(
                {
                    "role": "assistant",
                    "content": (
                        "对话失败：LLM/检索/向量化组件不可用。\n"
                        f"错误：{_format_llm_error(err_info, e)}\n"
                        f"{hint}"
                        "请检查：容器内是否能访问 `llm.chat.base_url`（网络/代理/证书/超时），以及 LLM 的 base_url/api_key 配置；"
                        "并确认 Qdrant 正常运行与 embedding 模型可用。"
                    ),
                    "meta": {"tools": [], "evidence": []},
                },
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
