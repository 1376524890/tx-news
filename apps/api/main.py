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

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
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
from tx_news.user_llm_config import UID_COOKIE, get_user_llm_config


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="tx-news API", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parents[2] / "apps" / "web" / "dist_public"

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


@app.get("/status", operation_id="status")
def status() -> dict[str, Any]:
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

    return {"dependencies": deps, "counts": counts}


@app.get("/search", response_model=list[SearchHit], operation_id="search_news")
def search(q: str = Query(min_length=1), limit: int = Query(default=10, ge=1, le=50)):
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    embedding_cfg = file_cfg.embedding or {}
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


@app.get("/signals", operation_id="list_signals")
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
                    "content": "未配置个人 LLM：请先访问 http://localhost:8001/ 设置 base_url/model/api_key。",
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
                        "content": "未配置个人 LLM：请先访问 http://localhost:8001/ 设置 base_url/model/api_key。",
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
