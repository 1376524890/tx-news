# Input: Cookie 用户标识 + Redis（用户 LLM 配置/队列监控）+ Postgres + NATS/Qdrant/MinIO/VLLM
# Output: 8001 配置服务 + /api/monitor 监控数据 + /monitor 简易监控页
# Pos: Admin/Config/Monitor 进程入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import DateTime, cast, func, select, text
from sqlalchemy.orm import Session

from tx_news.logging import configure_logging
from tx_news.settings import get_settings
from tx_news.storage.postgres import init_db, make_engine
from tx_news.user_llm_config import UID_COOKIE, clear_user_llm_config, get_user_llm_config, make_user_id, set_user_llm_config
from tx_news.db import Analysis, Article, RawDoc, Signal


configure_logging()
app = FastAPI(title="tx-news config", version="0.1.0")

ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT_DIR / "apps" / "web" / "dist_admin"


def _redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url)


def _pg_engine():
    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    return engine


def _safe_http(url: str, timeout: float = 1.5) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url)
        return {"ok": r.status_code < 500, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _safe_ping_redis() -> dict[str, Any]:
    try:
        r = _redis()
        ok = bool(r.ping())
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _safe_ping_postgres(engine) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _count_recent(session: Session, model, column, cutoff: datetime) -> tuple[int, int]:
    total = session.execute(select(func.count()).select_from(model)).scalar_one() or 0
    recent = (
        session.execute(select(func.count()).select_from(model).where(column >= cutoff)).scalar_one() or 0
    )
    return int(total), int(recent)


def _deep_recent(session: Session, cutoff: datetime) -> dict[str, Any]:
    try:
        deep_ts = cast(Analysis.data["deep_optimized_at"].astext, DateTime)
        recent = (
            session.execute(select(func.count()).select_from(Analysis).where(deep_ts >= cutoff)).scalar_one()
            or 0
        )
        total = (
            session.execute(
                select(func.count()).select_from(Analysis).where(Analysis.data.has_key("deep_optimized_at"))  # type: ignore[attr-defined]
            ).scalar_one()
            or 0
        )
        return {"total": int(total), "recent": int(recent)}
    except Exception as e:
        return {"total": None, "recent": None, "error": str(e)}


def _ensure_uid(request: Request) -> tuple[str, dict[str, str]]:
    uid = (request.cookies.get(UID_COOKIE) or "").strip()
    if uid:
        return uid, {}
    uid = make_user_id()
    # Cookie is shared across ports (same host); keep HttpOnly so JS can't read the key.
    return uid, {
        "Set-Cookie": f"{UID_COOKIE}={uid}; Path=/; HttpOnly; SameSite=Lax",
    }


@app.get("/health", operation_id="health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/monitor", operation_id="monitor")
def api_monitor(window_minutes: int = 5) -> Any:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    window = max(1, int(window_minutes))
    cutoff = now - timedelta(minutes=window)

    infra = {
        "api": _safe_http("http://127.0.0.1:18000/health"),
        "admin": _safe_http("http://127.0.0.1:8001/health"),
        "vllm": _safe_http("http://127.0.0.1:9999/v1/models"),
        "qdrant": _safe_http(settings.qdrant_url.rstrip("/") + "/collections"),
        "minio": _safe_http(settings.s3_endpoint.rstrip("/") + "/minio/health/live"),
        "nats": _safe_http("http://127.0.0.1:8222/healthz"),
        "redis": _safe_ping_redis(),
    }

    engine = _pg_engine()
    infra["postgres"] = _safe_ping_postgres(engine)

    queues: dict[str, Any] = {}
    try:
        r = _redis()
        queues = {
            "celery_default": int(r.llen("default")),
            "celery_unacked": int(r.hlen("unacked")),
            "analysis_queue": int(r.zcard("txnews:analysis_queue")),
            "analysis_queue_scores": int(r.hlen("txnews:analysis_queue:scores")),
        }
    except Exception as e:
        queues = {"error": str(e)}

    nats_info: dict[str, Any] = {}
    try:
        with httpx.Client(timeout=1.5) as client:
            r = client.get("http://127.0.0.1:8222/jsz?streams=1&consumers=1")
        if r.status_code < 500:
            payload = r.json()
            streams = payload.get("streams") or []
            if not isinstance(streams, list):
                nats_info = {"error": f"unexpected_streams_type={type(streams).__name__}"}
                streams = []
            stream = next((s for s in streams if isinstance(s, dict) and s.get("name") == settings.nats_stream), None)
            nats_info = {
                "stream": stream,
                "streams": len(streams),
            }
        else:
            nats_info = {"error": f"status={r.status_code}"}
    except Exception as e:
        nats_info = {"error": str(e)}

    metrics: dict[str, Any] = {}
    try:
        with Session(engine) as session:
            raw_total, raw_recent = _count_recent(session, RawDoc, RawDoc.created_at, cutoff)
            art_total, art_recent = _count_recent(session, Article, Article.created_at, cutoff)
            ana_total, ana_recent = _count_recent(session, Analysis, Analysis.created_at, cutoff)
            sig_total, sig_recent = _count_recent(session, Signal, Signal.created_at, cutoff)
            deep = _deep_recent(session, cutoff)

            metrics = {
                "raw": {
                    "total": raw_total,
                    "recent": raw_recent,
                    "rate_per_min": round(raw_recent / window, 2),
                },
                "articles": {
                    "total": art_total,
                    "recent": art_recent,
                    "rate_per_min": round(art_recent / window, 2),
                },
                "analysis": {
                    "total": ana_total,
                    "recent": ana_recent,
                    "rate_per_min": round(ana_recent / window, 2),
                },
                "deep_analysis": {
                    **deep,
                    "rate_per_min": (round((deep.get("recent") or 0) / window, 2) if isinstance(deep.get("recent"), int) else None),
                },
                "signals": {
                    "total": sig_total,
                    "recent": sig_recent,
                    "rate_per_min": round(sig_recent / window, 2),
                },
            }
    except Exception as e:
        metrics = {"error": str(e)}

    alerts: list[str] = []
    try:
        if isinstance(queues, dict):
            if queues.get("celery_unacked", 0) and queues.get("celery_unacked", 0) > 5:
                alerts.append("celery_unacked_high")
            if queues.get("celery_default", 0) and queues.get("celery_default", 0) > 500:
                alerts.append("celery_backlog_high")
            if queues.get("analysis_queue", 0) and queues.get("analysis_queue", 0) > 200:
                alerts.append("analysis_queue_backlog_high")
        if isinstance(metrics, dict):
            articles_recent = (metrics.get("articles") or {}).get("recent")
            analysis_recent = (metrics.get("analysis") or {}).get("recent")
            if isinstance(articles_recent, int) and isinstance(analysis_recent, int):
                if articles_recent > 0 and analysis_recent == 0:
                    alerts.append("analysis_zero_while_articles_ingest")
    except Exception:
        pass

    raw_rate = (metrics.get("raw") or {}).get("rate_per_min") if isinstance(metrics, dict) else None
    article_rate = (metrics.get("articles") or {}).get("rate_per_min") if isinstance(metrics, dict) else None
    analysis_rate = (metrics.get("analysis") or {}).get("rate_per_min") if isinstance(metrics, dict) else None
    deep_rate = (metrics.get("deep_analysis") or {}).get("rate_per_min") if isinstance(metrics, dict) else None
    signal_rate = (metrics.get("signals") or {}).get("rate_per_min") if isinstance(metrics, dict) else None

    nats_backlog = None
    try:
        stream = nats_info.get("stream") if isinstance(nats_info, dict) else None
        if isinstance(stream, dict):
            state = stream.get("state") if isinstance(stream.get("state"), dict) else {}
            nats_backlog = state.get("messages")
    except Exception:
        nats_backlog = None

    flow = {
        "window_minutes": window,
        "stages": [
            {
                "id": "nats",
                "label": "NATS raw",
                "backlog": nats_backlog,
                "in_rate": raw_rate,
                "out_rate": raw_rate,
            },
            {
                "id": "celery",
                "label": "Celery queue",
                "backlog": (queues.get("celery_default") if isinstance(queues, dict) else None),
                "in_rate": raw_rate,
                "out_rate": article_rate,
            },
            {
                "id": "analysis_queue",
                "label": "Analysis queue",
                "backlog": (queues.get("analysis_queue") if isinstance(queues, dict) else None),
                "in_rate": article_rate,
                "out_rate": analysis_rate,
            },
            {
                "id": "analysis",
                "label": "Analysis",
                "backlog": None,
                "in_rate": analysis_rate,
                "out_rate": deep_rate,
            },
            {
                "id": "deep",
                "label": "Deep analysis",
                "backlog": None,
                "in_rate": deep_rate,
                "out_rate": signal_rate,
            },
            {
                "id": "signals",
                "label": "Signals",
                "backlog": None,
                "in_rate": signal_rate,
                "out_rate": None,
            },
        ],
    }

    return {
        "now": now.isoformat(),
        "window_minutes": window,
        "infra": infra,
        "queues": queues,
        "nats": nats_info,
        "metrics": metrics,
        "alerts": alerts,
        "flow": flow,
    }


@app.get("/monitor", include_in_schema=False)
def monitor_page() -> Any:
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>TX-News Monitor</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 16px; }
    h1 { margin: 0 0 12px 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
    .card { border: 1px solid #ddd; padding: 12px; border-radius: 6px; }
    .ok { color: #0a0; font-weight: 600; }
    .bad { color: #a00; font-weight: 600; }
    .alert-pill { display: inline-block; padding: 4px 10px; margin: 4px 6px 4px 0; border-radius: 14px;
      background: linear-gradient(90deg,#ff6b6b,#ff922b); color: #fff; font-size: 12px; font-weight: 700; }
    .alert-wrap { padding: 10px 12px; border: 1px solid #f0c2c2; background: #fff5f5; border-radius: 8px; }
    pre { background: #f7f7f7; padding: 12px; border-radius: 6px; overflow: auto; }
    table { border-collapse: collapse; width: 100%; }
    td, th { border: 1px solid #ddd; padding: 6px 8px; text-align: left; }
    .flow { display: flex; align-items: stretch; gap: 10px; flex-wrap: wrap; }
    .flow-node { min-width: 170px; border: 1px solid #dfe3e6; border-radius: 8px; padding: 10px; background: #fff; }
    .flow-title { font-weight: 700; margin-bottom: 6px; }
    .flow-stat { font-size: 12px; color: #333; }
    .flow-arrow { align-self: center; font-size: 20px; color: #999; }
  </style>
</head>
<body>
  <h1>TX-News Monitor</h1>
  <div id="alerts"></div>
  <h2>Dataflow</h2>
  <div id="flow" class="flow"></div>
  <div id="summary" class="grid"></div>
  <h2>Rates</h2>
  <div class="grid">
    <div class="card"><b>Ingest/Analysis Rates</b><br/><canvas id="rateChart" width="420" height="160"></canvas></div>
    <div class="card"><b>Queue Sizes</b><br/><canvas id="queueChart" width="420" height="160"></canvas></div>
  </div>
  <h2>Queues</h2>
  <div id="queues"></div>
  <h2>Infra</h2>
  <div id="infra"></div>
  <h2>NATS</h2>
  <pre id="nats"></pre>
<script>
const history = { rates: [], queues: [] };
const MAX_POINTS = 60;
function drawLineChart(canvasId, series, labels){
  const c = document.getElementById(canvasId);
  if (!c) return;
  const ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle = '#ccc';
  ctx.beginPath();
  ctx.moveTo(30,10); ctx.lineTo(30,h-20); ctx.lineTo(w-10,h-20);
  ctx.stroke();
  const maxVal = Math.max(1, ...series.flat());
  const colors = ['#1f77b4','#2ca02c','#d62728','#9467bd'];
  series.forEach((vals, idx) => {
    ctx.strokeStyle = colors[idx % colors.length];
    ctx.beginPath();
    vals.forEach((v,i) => {
      const x = 30 + (w-40) * (i/(vals.length-1 || 1));
      const y = (h-20) - (h-40) * (v/maxVal);
      if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    });
    ctx.stroke();
  });
  ctx.fillStyle = '#333';
  labels.forEach((label, idx) => {
    ctx.fillStyle = colors[idx % colors.length];
    ctx.fillText(label, 34 + idx*110, 12);
  });
}

async function fetchMonitor() {
  const res = await fetch('/api/monitor');
  return await res.json();
}
function badge(ok){ return ok ? '<span class="ok">OK</span>' : '<span class="bad">FAIL</span>'; }
function fmt(v){ return (v === null || v === undefined) ? '-' : v; }
function renderTable(obj){
  let rows = Object.entries(obj).map(([k,v]) => `<tr><th>${k}</th><td>${typeof v === 'object' ? JSON.stringify(v) : v}</td></tr>`).join('');
  return `<table>${rows}</table>`;
}
async function refresh() {
  const data = await fetchMonitor();
  const metrics = data.metrics || {};
  const alerts = data.alerts || [];
  document.getElementById('alerts').innerHTML = alerts.length
    ? '<div class="alert-wrap"><b>Alerts</b><br/>' + alerts.map(a => `<span class="alert-pill">${a}</span>`).join('') + '</div>'
    : '';

  const flow = data.flow || {};
  const stages = flow.stages || [];
  let flowHtml = '';
  stages.forEach((s, i) => {
    flowHtml += `<div class="flow-node">
      <div class="flow-title">${s.label || s.id}</div>
      <div class="flow-stat">backlog: ${fmt(s.backlog)}</div>
      <div class="flow-stat">in: ${fmt(s.in_rate)}/min</div>
      <div class="flow-stat">out: ${fmt(s.out_rate)}/min</div>
    </div>`;
    if (i < stages.length - 1) {
      flowHtml += `<div class="flow-arrow">➜</div>`;
    }
  });
  document.getElementById('flow').innerHTML = flowHtml;
  const summary = [];
  if (metrics.raw) summary.push(`<div class="card"><b>Raw</b><br/>total=${metrics.raw.total} recent=${metrics.raw.recent} rate=${metrics.raw.rate_per_min}/min</div>`);
  if (metrics.articles) summary.push(`<div class="card"><b>Articles</b><br/>total=${metrics.articles.total} recent=${metrics.articles.recent} rate=${metrics.articles.rate_per_min}/min</div>`);
  if (metrics.analysis) summary.push(`<div class="card"><b>Analysis</b><br/>total=${metrics.analysis.total} recent=${metrics.analysis.recent} rate=${metrics.analysis.rate_per_min}/min</div>`);
  if (metrics.deep_analysis) summary.push(`<div class="card"><b>Deep Analysis</b><br/>total=${metrics.deep_analysis.total} recent=${metrics.deep_analysis.recent} rate=${metrics.deep_analysis.rate_per_min}/min</div>`);
  if (metrics.signals) summary.push(`<div class="card"><b>Signals</b><br/>total=${metrics.signals.total} recent=${metrics.signals.recent} rate=${metrics.signals.rate_per_min}/min</div>`);
  document.getElementById('summary').innerHTML = summary.join('');

  document.getElementById('queues').innerHTML = renderTable(data.queues || {});
  const infraRows = Object.entries(data.infra || {}).map(([k,v]) => {
    const ok = v && v.ok;
    return `<tr><th>${k}</th><td>${badge(ok)} ${v && v.status ? 'status=' + v.status : ''} ${v && v.error ? 'err=' + v.error : ''}</td></tr>`;
  }).join('');
  document.getElementById('infra').innerHTML = `<table>${infraRows}</table>`;

  document.getElementById('nats').textContent = JSON.stringify(data.nats || {}, null, 2);

  const ratePoint = [
    (metrics.raw && metrics.raw.rate_per_min) || 0,
    (metrics.articles && metrics.articles.rate_per_min) || 0,
    (metrics.analysis && metrics.analysis.rate_per_min) || 0,
    (metrics.deep_analysis && metrics.deep_analysis.rate_per_min) || 0,
  ];
  history.rates.push(ratePoint);
  if (history.rates.length > MAX_POINTS) history.rates.shift();

  const queuePoint = [
    (data.queues && data.queues.celery_default) || 0,
    (data.queues && data.queues.analysis_queue) || 0,
  ];
  history.queues.push(queuePoint);
  if (history.queues.length > MAX_POINTS) history.queues.shift();

  const seriesRates = [0,1,2,3].map(i => history.rates.map(p => p[i]));
  drawLineChart('rateChart', seriesRates, ['raw/min','articles/min','analysis/min','deep/min']);
  const seriesQueues = [0,1].map(i => history.queues.map(p => p[i]));
  drawLineChart('queueChart', seriesQueues, ['celery_default','analysis_queue']);
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""
    return HTMLResponse(html)


# Mount /assets for Vue SPA
if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")


@app.get("/", include_in_schema=False)
def ui_index() -> Any:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>TX-News Config</h1><p>UI build not found. Run: npm --prefix apps/web run build:admin</p>")
    return FileResponse(str(index))


class UserLLMConfigIn(BaseModel):
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str = Field(min_length=1)


@app.get("/api/config", operation_id="get_user_llm_config")
def api_get_config(request: Request) -> Any:
    uid, headers = _ensure_uid(request)
    cfg = get_user_llm_config(_redis(), uid)
    out = {"uid": uid, "configured": bool(cfg), "config": (cfg.masked() if cfg else None)}
    return JSONResponse(content=out, headers=headers)


@app.post("/api/config", operation_id="set_user_llm_config")
def api_set_config(request: Request, body: UserLLMConfigIn) -> Any:
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
    return JSONResponse(content=out, headers=headers)


@app.post("/api/config/clear", operation_id="clear_user_llm_config")
def api_clear_config(request: Request) -> Any:
    uid, headers = _ensure_uid(request)
    clear_user_llm_config(_redis(), uid)
    out = {"uid": uid, "configured": False, "config": None}
    return JSONResponse(content=out, headers=headers)


if __name__ == "__main__":
    # Local dev helper (not used by scripts/start.sh)
    import uvicorn

    port = int(os.environ.get("PORT") or "8001")
    uvicorn.run("apps.admin.main:app", host="0.0.0.0", port=port, reload=True)
