# Input: HTTP 请求 + Postgres/Qdrant/Redis/NATS 等依赖
# Output: FastAPI 路由与静态 UI（/、/admin、/search、/chat、/chat/stream 等）
# Pos: API 进程入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from redis import Redis
from sqlalchemy import text

import nats

from tx_news.agent.txnews_agent import AgentChatError, TxNewsAgent
from tx_news.embedding.embedder import DEFAULT_EMBEDDING_MODEL, build_embedder
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


app = FastAPI(title="tx-news API", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

ROOT_DIR = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT_DIR / ".run"
LOG_DIR = ROOT_DIR / "var" / "log"


class SearchHit(BaseModel):
    canonical_id: str
    score: float
    title: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    event_type: str | None = None
    tickers: list[dict[str, Any]] = []


@app.get("/health", operation_id="health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def ui_index() -> Response:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>TX-News</h1><p>UI not found.</p>")
    return FileResponse(str(index))


@app.get("/admin", include_in_schema=False)
def ui_admin() -> Response:
    page = STATIC_DIR / "admin.html"
    if not page.exists():
        return HTMLResponse("<h1>TX-News Admin</h1><p>Admin UI not found.</p>")
    return FileResponse(str(page))


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
                tickers=(an.data.get("tickers") if an else []) or [],
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


async def _check_nats(url: str) -> tuple[bool, str | None]:
    try:
        nc = await nats.connect(url, connect_timeout=2)
        await nc.drain()
        return True, None
    except Exception as e:
        return False, str(e)


async def _nats_js_stats(url: str, *, stream: str, consumer: str) -> dict[str, Any]:
    nc = await nats.connect(url, connect_timeout=2)
    js = nc.jetstream()
    out: dict[str, Any] = {"stream": stream, "consumer": consumer}
    try:
        sinfo = await js.stream_info(stream)
        out["messages"] = getattr(sinfo.state, "messages", None)
        out["bytes"] = getattr(sinfo.state, "bytes", None)
    except Exception as e:
        out["stream_error"] = str(e)
    try:
        cinfo = await js.consumer_info(stream, consumer)
        out["num_pending"] = getattr(cinfo, "num_pending", None)
        out["num_ack_pending"] = getattr(cinfo, "num_ack_pending", None)
        out["num_redelivered"] = getattr(cinfo, "num_redelivered", None)
    except Exception as e:
        out["consumer_error"] = str(e)
    await nc.drain()
    return out


def _tail_file(path: Path, n: int = 200) -> str:
    n = max(1, min(int(n), 2000))
    if not path.exists():
        return ""
    # Fast tail: read backwards in blocks.
    block_size = 8192
    data = b""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        pos = size
        while pos > 0 and data.count(b"\n") <= n:
            step = min(block_size, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            if pos == 0:
                break
    lines = data.splitlines()[-n:]
    try:
        return b"\n".join(lines).decode("utf-8", errors="replace")
    except Exception:
        return "\n".join([line.decode("utf-8", errors="replace") for line in lines])


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _proc_cmdline(pid: int) -> str | None:
    p = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = p.read_bytes()
        if not raw:
            return None
        return " ".join([x for x in raw.decode("utf-8", errors="replace").split("\x00") if x])
    except Exception:
        return None


@app.get("/admin/status", operation_id="admin_status")
def admin_status() -> dict[str, Any]:
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    deps: dict[str, Any] = {}

    # Postgres + basic counts
    try:
        with engine.connect() as conn:
            counts = {
                "articles": int(conn.execute(text("select count(*) from articles")).scalar() or 0),
                "analyses": int(conn.execute(text("select count(*) from analyses")).scalar() or 0),
                "raw_documents": int(conn.execute(text("select count(*) from raw_documents")).scalar() or 0),
                "signals": int(conn.execute(text("select count(*) from signals")).scalar() or 0),
                "a_share_basic": int(conn.execute(text("select count(*) from a_share_basic")).scalar() or 0),
            }
            latest = {
                "signal_at": (
                    conn.execute(text("select max(created_at) from signals")).scalar()
                ),
                "article_at": (
                    conn.execute(text("select max(updated_at) from articles")).scalar()
                ),
            }
        deps["postgres"] = {"ok": True}
    except Exception as e:
        counts = {}
        latest = {"signal_at": None, "article_at": None}
        deps["postgres"] = {"ok": False, "error": str(e)}

    # Redis
    try:
        r = Redis.from_url(settings.redis_url)
        r.ping()
        deps["redis"] = {"ok": True}
    except Exception as e:
        deps["redis"] = {"ok": False, "error": str(e)}

    # NATS
    ok, err = asyncio.run(_check_nats(settings.nats_url))
    deps["nats"] = {"ok": ok, "error": err}
    js_stats = asyncio.run(_nats_js_stats(settings.nats_url, stream=settings.nats_stream, consumer="txnews_raw_bridge"))

    # MinIO
    try:
        from tx_news.storage.minio import S3Client

        s3 = S3Client(
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            bucket=settings.s3_bucket,
        )
        s3._client().list_buckets()  # type: ignore[attr-defined]
        deps["minio"] = {"ok": True}
    except Exception as e:
        deps["minio"] = {"ok": False, "error": str(e)}

    # Qdrant
    try:
        q = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection)
        q._client().get_collections()  # type: ignore[attr-defined]
        deps["qdrant"] = {"ok": True}
    except Exception as e:
        deps["qdrant"] = {"ok": False, "error": str(e)}

    # Normalize latest datetimes to ISO strings
    latest_out = {}
    for k, v in latest.items():
        latest_out[k] = v.isoformat() if hasattr(v, "isoformat") and v else None

    return {
        "dependencies": deps,
        "counts": counts,
        "latest": latest_out,
        "nats_jetstream": js_stats,
    }


@app.get("/admin/processes", operation_id="admin_processes")
def admin_processes() -> dict[str, Any]:
    processes: list[dict[str, Any]] = []
    for pidfile in sorted(RUN_DIR.glob("*.pid")):
        name = pidfile.stem
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except Exception:
            processes.append({"name": name, "pid": None, "alive": False, "error": "invalid pidfile"})
            continue
        alive = _process_alive(pid)
        processes.append(
            {
                "name": name,
                "pid": pid,
                "alive": alive,
                "cmdline": _proc_cmdline(pid) if alive else None,
                "pidfile": str(pidfile.relative_to(ROOT_DIR)),
            }
        )
    return {"processes": processes}


@app.get("/admin/pipeline", operation_id="admin_pipeline")
def admin_pipeline(minutes: int = Query(default=60, ge=5, le=1440)) -> dict[str, Any]:
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    with engine.connect() as conn:
        window_raw = int(
            conn.execute(text("select count(*) from raw_documents where created_at >= now() - (:m || ' minutes')::interval"), {"m": minutes}).scalar() or 0
        )
        window_versions = int(
            conn.execute(text("select count(*) from article_versions where created_at >= now() - (:m || ' minutes')::interval"), {"m": minutes}).scalar() or 0
        )
        window_analyses = int(
            conn.execute(text("select count(*) from analyses where created_at >= now() - (:m || ' minutes')::interval"), {"m": minutes}).scalar() or 0
        )
        kinds = conn.execute(
            text(
                "select kind, count(*) as c from signals where created_at >= now() - (:m || ' minutes')::interval group by kind order by c desc"
            ),
            {"m": minutes},
        ).all()
        window_signals = {k: int(c) for k, c in kinds}

        latest_raw = conn.execute(
            text(
                "select source_id, url, fetched_at, created_at from raw_documents order by created_at desc limit 1"
            )
        ).mappings().first()
        latest_analysis = conn.execute(
            text("select canonical_id, event_type, created_at from analyses order by created_at desc limit 1")
        ).mappings().first()
        latest_version = conn.execute(
            text(
                "select canonical_id, source_id, url, coalesce(published_at, fetched_at) as t from article_versions order by coalesce(published_at, fetched_at) desc limit 1"
            )
        ).mappings().first()

        max_raw = conn.execute(text("select max(created_at) from raw_documents")).scalar()
        max_an = conn.execute(text("select max(created_at) from analyses")).scalar()
        lag_seconds = None
        if max_raw and max_an:
            lag = (max_raw - max_an).total_seconds()
            lag_seconds = float(lag)

    def _dt(v):
        return v.isoformat() if hasattr(v, "isoformat") and v else None

    return {
        "window_minutes": minutes,
        "counts": {
            "raw_documents": window_raw,
            "article_versions": window_versions,
            "analyses": window_analyses,
            "signals_by_kind": window_signals,
        },
        "latest": {
            "raw_document": {
                "source_id": latest_raw.get("source_id") if latest_raw else None,
                "url": latest_raw.get("url") if latest_raw else None,
                "fetched_at": _dt(latest_raw.get("fetched_at")) if latest_raw else None,
                "created_at": _dt(latest_raw.get("created_at")) if latest_raw else None,
            }
            if latest_raw
            else None,
            "article_version": {
                "canonical_id": latest_version.get("canonical_id") if latest_version else None,
                "source_id": latest_version.get("source_id") if latest_version else None,
                "url": latest_version.get("url") if latest_version else None,
                "time": _dt(latest_version.get("t")) if latest_version else None,
            }
            if latest_version
            else None,
            "analysis": {
                "canonical_id": latest_analysis.get("canonical_id") if latest_analysis else None,
                "event_type": latest_analysis.get("event_type") if latest_analysis else None,
                "created_at": _dt(latest_analysis.get("created_at")) if latest_analysis else None,
            }
            if latest_analysis
            else None,
        },
        "lag_seconds_raw_minus_analysis": lag_seconds,
    }


def _read_masterdata_cache() -> dict[str, Any] | None:
    candidates = [
        ROOT_DIR / "var" / "cache" / "a_share" / "stock_basic.json",
        ROOT_DIR / "var" / "cache" / "tushare" / "stock_basic.json",
    ]
    for cache_path in candidates:
        if not cache_path.exists():
            continue
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            rows_obj = payload.get("rows")
            return {
                "path": str(cache_path.relative_to(ROOT_DIR)),
                "source": payload.get("source"),
                "fetched_at": payload.get("fetched_at"),
                "rows": len(rows_obj) if isinstance(rows_obj, list) else None,
            }
        except Exception:
            return {"path": str(cache_path.relative_to(ROOT_DIR)), "error": "invalid json"}
    return None


@app.get("/admin/masterdata", operation_id="admin_masterdata")
def admin_masterdata() -> dict[str, Any]:
    return {"stock_basic_cache": _read_masterdata_cache()}


@app.get("/admin/tushare", operation_id="admin_tushare")
def admin_tushare() -> dict[str, Any]:
    # Backward-compatible alias.
    return {"stock_basic_cache": _read_masterdata_cache()}


@app.get("/admin/logs/{name}", operation_id="admin_logs")
def admin_logs(name: str, n: int = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
    allowed = {
        "bootstrap": LOG_DIR / "bootstrap.log",
        "api": LOG_DIR / "api.log",
        "collector": LOG_DIR / "collector.log",
        "nats_bridge": LOG_DIR / "nats_bridge.log",
        "celery_worker": LOG_DIR / "celery_worker.log",
    }
    path = allowed.get(name)
    if not path:
        return {"error": "not_allowed", "allowed": sorted(allowed.keys())}
    return {"name": name, "path": str(path.relative_to(ROOT_DIR)), "tail": _tail_file(path, n=n)}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    max_steps: int = 6
    recent_minutes: int = 180


class ChatResponse(BaseModel):
    message: dict[str, Any]

def _sse(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@app.post("/chat", response_model=ChatResponse, operation_id="chat_agent")
def chat_agent(req: ChatRequest) -> ChatResponse:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    llm = settings.resolve_llm(file_cfg)
    api_key = llm.get("api_key")
    model = llm.get("model") or "qwen3-max"
    base_url = llm.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"

    if not api_key:
        return ChatResponse(
            message={
                "role": "assistant",
                "content": (
                    "未配置 LLM API Key：请在环境变量 `TXNEWS_LLM_API_KEY`（或兼容的 `DASHSCOPE_API_KEY` / `OPENAI_API_KEY`），"
                    "或 `config/config.yaml` 的 `llm.api_key` 中配置后再试。"
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
def chat_agent_stream(req: ChatRequest) -> StreamingResponse:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    llm = settings.resolve_llm(file_cfg)
    api_key = llm.get("api_key")
    model = llm.get("model") or "qwen3-max"
    base_url = llm.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_seconds = int(llm.get("timeout_seconds") or 60)

    def gen():
        yield _sse("ready", {"ok": True})
        if not api_key:
            yield _sse(
                "done",
                {
                    "role": "assistant",
                    "content": (
                        "未配置 LLM API Key：请在环境变量 `TXNEWS_LLM_API_KEY`（或兼容的 `DASHSCOPE_API_KEY` / `OPENAI_API_KEY`），"
                        "或 `config/config.yaml` 的 `llm.api_key` 中配置后再试。"
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
            for ev in agent.run_stream(
                messages=[m.model_dump() for m in req.messages],
                max_steps=req.max_steps,
                recent_minutes=req.recent_minutes,
            ):
                t = ev.get("type")
                if t == "delta":
                    yield _sse("delta", {"content": ev.get("content") or ""})
                elif t == "tool_call":
                    yield _sse("tool", {"name": ev.get("name"), "arguments": ev.get("arguments")})
                elif t == "done":
                    yield _sse("done", ev.get("message") or {})
                    return
            yield _sse(
                "done",
                {"role": "assistant", "content": "对话失败：无返回", "meta": {"tools": [], "evidence": []}},
            )
        except AgentChatError as e:
            yield _sse(
                "done",
                {"role": "assistant", "content": f"对话失败：{e}", "meta": {"tools": [], "evidence": []}},
            )
        except Exception as e:
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
