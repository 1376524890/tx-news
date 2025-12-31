# Input: Cookie 用户标识 + Redis（保存用户 LLM 配置）+ Vue admin 构建产物
# Output: 8001 配置服务：UI + /api/config（设置 base_url/model/api_key）
# Pos: Admin/Config 进程入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from redis import Redis

from tx_news.logging import configure_logging
from tx_news.settings import get_settings
from tx_news.user_llm_config import UID_COOKIE, clear_user_llm_config, get_user_llm_config, make_user_id, set_user_llm_config


configure_logging()
app = FastAPI(title="tx-news config", version="0.1.0")

ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT_DIR / "apps" / "web" / "dist_admin"


def _redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url)


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
