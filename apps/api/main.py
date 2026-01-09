# Input: HTTP 请求 + Postgres/Qdrant/Redis 等依赖 + 用户 Cookie（可选）
# Output: 公网/用户侧 API + 对话 UI（8000；不提供运维管理台 UI）
# Pos: Public API 进程入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
import logging
import time
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

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
    get_a_share,
    get_analysis,
    get_article,
    get_event_canonical_ids,
    get_latest_version,
    init_db,
    list_signals,
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
def status() -> Response:
    """
    Public-facing status for the chat UI (keep minimal; do not expose admin UI).
    """
    settings = get_settings()
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

def _config_url(request: Request) -> str:
    # Prefer the public same-origin config page (works behind reverse proxies / tunnels).
    base = str(getattr(request, "base_url", "") or "").rstrip("/")
    if base:
        return f"{base}/config"
    return "/config"


@app.post("/chat", response_model=ChatResponse, operation_id="chat_agent")
def chat_agent(req: ChatRequest, request: Request) -> ChatResponse:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    llm = settings.resolve_llm_chat(file_cfg)
    user_llm = _resolve_user_llm(request) or {}
    api_key = user_llm.get("api_key") or llm.get("api_key")
    model = user_llm.get("model") or llm.get("model") or "qwen3-max"
    base_url = user_llm.get("base_url") or llm.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"

    if not api_key:
        if settings.require_user_llm:
            return ChatResponse(
                message={
                    "role": "assistant",
                    "content": f"未配置个人 LLM：请先访问 {_config_url(request)} 设置 base_url/model/api_key。",
                    "meta": {"tools": [], "evidence": []},
                }
            )
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
        agent = TxNewsAgent(api_key=str(api_key), model=str(model), base_url=str(base_url))
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
        return ChatResponse(
            message={
                "role": "assistant",
                "content": (
                    "对话失败：系统检索/向量化组件不可用。\n"
                    f"错误：{e}\n"
                    "请检查：`config/config.yaml` 的 `embedding.model_name`（建议本地模型目录或 `BAAI/bge-small-zh-v1.5`），"
                    "以及 Qdrant 是否正常运行。"
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
                yield _sse(
                    "done",
                    {
                        "role": "assistant",
                        "content": f"未配置个人 LLM：请先访问 {_config_url(request)} 设置 base_url/model/api_key。",
                        "meta": {"tools": [], "evidence": []},
                    },
                )
                return
            yield _sse(
                "done",
                {
                    "role": "assistant",
                    "content": (
                    "未配置 LLM API Key：请在环境变量 `TXNEWS_LLM_API_KEY`（或兼容的 `DASHSCOPE_API_KEY` / `OPENAI_API_KEY`），"
                    "或 `config/config.yaml` 的 `llm.chat.api_key`（兼容 `llm.api_key`）中配置后再试。"
                    ),
                    "meta": {"tools": [], "evidence": []},
                },
            )
            return
        try:
            agent = TxNewsAgent(
                api_key=str(api_key),
                model=str(model),
                base_url=str(base_url),
                timeout_seconds=timeout_seconds,
            )
            first_delta_at: float | None = None
            delta_chars = 0
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
                        # Avoid log spam: only log progress on DEBUG level.
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
                    yield _sse("done", ev.get("message") or {})
                    return
            logger.warning("chat_stream ended without done trace=%s", trace_id)
            yield _sse(
                "done",
                {"role": "assistant", "content": "对话失败：无返回", "meta": {"tools": [], "evidence": []}},
            )
        except AgentChatError as e:
            logger.warning("chat_stream agent error trace=%s err=%s", trace_id, e)
            yield _sse(
                "done",
                {"role": "assistant", "content": f"对话失败：{e}", "meta": {"tools": [], "evidence": []}},
            )
        except Exception as e:
            logger.exception("chat_stream exception trace=%s", trace_id)
            yield _sse(
                "done",
                {
                    "role": "assistant",
                    "content": (
                        "对话失败：系统检索/向量化/LLM 组件不可用。\n"
                        f"错误：{e}\n"
                        "请检查：`config/config.yaml` 的 `embedding.model_name`、Qdrant 是否正常运行，以及 LLM 的 base_url/api_key 配置。"
                    ),
                    "meta": {"tools": [], "evidence": []},
                },
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
