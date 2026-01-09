# Input: canonical 文章内容 + Qdrant 相似检索 + DashScope API（可选）
# Output: 深分析后的结构化结果写回 analyses（幂等跳过/锁防重），并发出 signal
# Pos: Deep Path 任务（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from redis import Redis

from tx_news.analysis.dashscope import DashScopeClient
from tx_news.analysis.rules import EventWindowPlanner
from tx_news.embedding.embedder import DEFAULT_EMBEDDING_MODEL, build_embedder
from tx_news.settings import get_settings
from tx_news.storage.postgres import get_analysis, get_article, get_latest_version, init_db, make_engine, upsert_analysis, insert_signal
from tx_news.storage.qdrant import QdrantStore, scored_point_canonical_id
from tx_news.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

DEEP_LOCK_TTL_SECONDS = 20 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _release_redis_lock(redis: Redis, key: str, token: str) -> None:
    redis.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
        1,
        key,
        token,
    )


@celery_app.task(name="tx_news.tasks.deep_analysis.deep_optimize")
def deep_optimize(canonical: dict[str, Any]) -> dict[str, Any]:
    """
    Agentic-style deep analysis:
    - retrieve semantically related articles (vector search)
    - build a compact evidence bundle
    - ask LLM to produce a refined structured result
    - upsert DB result
    """
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    # Cost policy:
    # - GPU mode: prefer local vLLM (llm.deep); fall back to cloud (llm.chat) on failure.
    # - CPU mode: resolve_llm_deep() already falls back to llm.chat.
    llm = settings.resolve_llm_deep(file_cfg)
    llm_fallback = settings.resolve_llm_chat(file_cfg)

    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    canonical_id = str(canonical.get("canonical_id") or "").strip()
    if not canonical_id:
        return {"skipped": True, "reason": "missing_canonical_id"}

    current_checksum = str(canonical.get("checksum") or "").strip()
    current_analysis = get_analysis(engine, canonical_id)
    if current_analysis and current_checksum:
        data = current_analysis.data if isinstance(current_analysis.data, dict) else {}
        meta = data.get("_txnews") if isinstance(data.get("_txnews"), dict) else {}
        prev_checksum = str(meta.get("deep_text_checksum") or "").strip()
        if prev_checksum and prev_checksum == current_checksum and data.get("deep_optimized_at"):
            logger.info("deep_analysis skipped canonical_id=%s reason=same_checksum", canonical_id)
            return {"skipped": True, "reason": "same_checksum"}

    lock_key = f"txnews:lock:deep:{canonical_id}"
    lock_token = secrets.token_hex(8)
    try:
        redis = Redis.from_url(settings.redis_url)
        got_lock = bool(redis.set(lock_key, lock_token, nx=True, ex=DEEP_LOCK_TTL_SECONDS))
    except Exception:
        got_lock = True
        redis = None  # type: ignore[assignment]
    if not got_lock:
        logger.info("deep_analysis skipped canonical_id=%s reason=lock_busy", canonical_id)
        return {"skipped": True, "reason": "lock_busy"}

    embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
    embedder, qdrant_strategy = build_embedder(embedding_cfg)
    model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)

    try:
        vector = embedder.embed(canonical["text"])
        qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection).resolve_collection_for_embedding(
            vector_size=len(vector),
            model_name_or_path=model_name,
            strategy=qdrant_strategy,
        )
        related = qdrant.search(vector=vector, limit=8)

        evidence: list[dict[str, Any]] = []
        for p in related:
            cid = scored_point_canonical_id(p) or str(p.id)
            v = get_latest_version(engine, cid)
            a = get_article(engine, cid)
            if not a:
                continue
            evidence.append(
                {
                    "canonical_id": cid,
                    "score": float(p.score or 0.0),
                    "title": a.title,
                    "url": v.url if v else None,
                    "published_at": v.published_at.isoformat() if v and v.published_at else None,
                }
            )

        planner = EventWindowPlanner(event_windows_minutes=file_cfg.event_windows_minutes or {})
        event_type = (current_analysis.event_type if current_analysis else "other") if current_analysis else "other"
        minutes = planner.window_minutes(event_type)

        system = (
            "你是金融新闻分析助手。请只输出一个 JSON 对象，不要输出任何多余文本。"
            "你需要在已有初步分析的基础上，结合相关证据进行二次推理与修正。"
            "输出字段：event_type, entities, tickers, impact, index_view, evidence。"
            "注意：不要输出新闻全文，不要输出长段引用。"
        )
        user = (
            f"目标新闻标题：{canonical.get('title') or ''}\n"
            f"目标新闻正文：{canonical.get('text','')[:5000]}\n\n"
            f"初步分析：{current_analysis.data if current_analysis else None}\n\n"
            f"相似新闻证据（TopN，含时间与链接）：{evidence}\n\n"
            f"请在 {minutes} 分钟的事件窗口假设下进行逻辑化分析，修正与补全结构化结果。"
        )

        def _call(llm_cfg: dict[str, Any]) -> dict[str, Any]:
            client = DashScopeClient(
                api_key=str(llm_cfg.get("api_key")) if llm_cfg.get("api_key") else None,
                model=str(llm_cfg.get("model") or "deepseekr1-merged"),
                base_url=str(llm_cfg.get("base_url") or "http://127.0.0.1:9999/v1"),
                timeout_seconds=int(llm_cfg.get("timeout_seconds") or 120),
            )
            return client.chat_json(system=system, user=user)

        try:
            out = _call(dict(llm or {}))
        except Exception as e:
            # Local vLLM down/misconfigured → fall back to cloud LLM (if api_key is configured).
            logger.warning("deep_analysis primary llm failed; try fallback: %s", e)
            try:
                if llm_fallback and llm_fallback.get("api_key"):
                    out = _call(dict(llm_fallback or {}))
                else:
                    return {"skipped": True, "reason": "llm_failed", "error": str(e)}
            except Exception as e2:
                logger.warning("deep_analysis fallback llm failed; skipped canonical_id=%s err=%s", canonical_id, e2)
                return {"skipped": True, "reason": "llm_failed", "error": str(e2)}

        base = current_analysis.data if current_analysis and isinstance(current_analysis.data, dict) else {}
        meta = base.get("_txnews") if isinstance(base.get("_txnews"), dict) else {}
        result = {
            **base,
            **out,
            "canonical_id": canonical_id,
            "deep_optimized_at": utcnow().isoformat(),
            "_txnews": {**meta, "deep_text_checksum": current_checksum or None},
        }
        upsert_analysis(engine, canonical_id, event_type=str(result.get("event_type", event_type)), data=result, llm_used=True)
        insert_signal(engine, canonical_id, "deep_analysis_updated", {"event_type": result.get("event_type", event_type)})
        return {"updated": True}
    finally:
        if got_lock and redis is not None:
            try:
                _release_redis_lock(redis, lock_key, lock_token)
            except Exception:
                pass
