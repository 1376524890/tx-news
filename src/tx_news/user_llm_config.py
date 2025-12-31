# Input: Request cookie uid + Redis + 用户提交的 LLM 配置
# Output: 读取/写入单用户 LLM 配置（用于 chat 请求按用户分摊成本）
# Pos: 用户侧在线 LLM 配置存储（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from redis import Redis


UID_COOKIE = "txnews_uid"
REDIS_KEY_PREFIX = "txnews:user_llm:"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_user_id() -> str:
    # short, URL/cookie-friendly id; good enough for local multi-user differentiation
    return secrets.token_hex(12)


def redis_key(uid: str) -> str:
    return f"{REDIS_KEY_PREFIX}{uid}"


@dataclass(frozen=True)
class UserLLMConfig:
    base_url: str
    model: str
    api_key: str
    updated_at: str

    def masked(self) -> dict[str, Any]:
        key = self.api_key or ""
        masked = (key[:4] + "…" + key[-4:]) if len(key) >= 10 else ("***" if key else "")
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_masked": masked,
            "api_key_set": bool(key),
            "updated_at": self.updated_at,
        }


def get_user_llm_config(r: Redis, uid: str) -> UserLLMConfig | None:
    raw = r.get(redis_key(uid))
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    base_url = str(obj.get("base_url") or "").strip()
    model = str(obj.get("model") or "").strip()
    api_key = str(obj.get("api_key") or "").strip()
    updated_at = str(obj.get("updated_at") or "").strip() or utcnow_iso()
    if not base_url or not model or not api_key:
        return None
    return UserLLMConfig(base_url=base_url, model=model, api_key=api_key, updated_at=updated_at)


def set_user_llm_config(
    r: Redis, *, uid: str, base_url: str, model: str, api_key: str, ttl_seconds: int | None = None
) -> None:
    obj = {
        "base_url": (base_url or "").strip(),
        "model": (model or "").strip(),
        "api_key": (api_key or "").strip(),
        "updated_at": utcnow_iso(),
    }
    payload = json.dumps(obj, ensure_ascii=False)
    key = redis_key(uid)
    if ttl_seconds and int(ttl_seconds) > 0:
        r.setex(key, int(ttl_seconds), payload)
    else:
        r.set(key, payload)


def clear_user_llm_config(r: Redis, uid: str) -> None:
    r.delete(redis_key(uid))

