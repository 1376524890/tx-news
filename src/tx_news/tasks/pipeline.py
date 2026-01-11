# Input: NATS raw payload + Postgres/MinIO/Qdrant/embedding/LLM（可选）
# Output: canonical 入库、向量 upsert、analysis upsert/幂等跳过、signals 写入
# Pos: 主流水线任务定义（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from celery import chain
from redis import Redis

from tx_news.analysis.dashscope import DashScopeClient
from tx_news.analysis.rules import EventWindowPlanner, classify_event_type, pick_key_entity, stable_event_id
from tx_news.analysis.tickers import TickerMatcher
from tx_news.db import Article
from tx_news.dedup.lsh import LshDeduper, LshIndex
from tx_news.embedding.embedder import DEFAULT_EMBEDDING_MODEL, build_embedder
from tx_news.normalize.metadata import extract_published_at
from tx_news.normalize.readability import ReadabilityExtractor
from tx_news.settings import get_settings
from tx_news.storage.minio import S3Client
from tx_news.storage.postgres import (
    get_article,
    get_analysis,
    init_db,
    insert_signal,
    insert_version,
    load_a_share_name_map,
    make_engine,
    upsert_analysis,
    upsert_article,
)
from tx_news.storage.qdrant import QdrantStore, scored_point_canonical_id
from tx_news.tasks.celery_app import celery_app
from tx_news.tasks.deep_analysis import deep_optimize

logger = logging.getLogger(__name__)

ANALYZE_LOCK_TTL_SECONDS = 10 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_local_llm_base_url(base_url: str) -> bool:
    """
    Best-effort detection for "local" OpenAI-compatible endpoints.

    Used to decide whether api_key can be omitted for llm.deep (e.g. host vLLM or in-cluster service).
    """
    s = (base_url or "").strip()
    if not s:
        return False
    try:
        p = urlparse(s)
        host = (p.hostname or "").strip().lower()
        scheme = (p.scheme or "").strip().lower()
    except Exception:
        return False

    if host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}:
        return True

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private:
            return True
    except ValueError:
        pass

    # docker-compose / k8s service DNS (often single-label) usually means in-network local service.
    if scheme == "http" and host and "." not in host:
        return True

    return host.endswith((".cluster.local", ".local"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_tickers(value: Any) -> list[dict[str, Any]]:
    """
    Ensure tickers schema is stable for API/UI.
    - preferred: list[{"ts_code": "...", ...}]
    - accepted fallback: list[str] -> list[{"ts_code": str}]
    """
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
            continue
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append({"ts_code": s})
    return out


def _release_redis_lock(redis: Redis, key: str, token: str) -> None:
    # Atomic "check-and-del" to avoid releasing a lock acquired by someone else.
    redis.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
        1,
        key,
        token,
    )


@celery_app.task(name="tx_news.tasks.pipeline.normalize_raw")
def normalize_raw(raw: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    s3 = S3Client(
        endpoint_url=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
    )
    html = s3.get_bytes(raw["raw_bytes_s3_key"])
    extractor = ReadabilityExtractor()
    extracted = extractor.extract(html)
    published_at = extract_published_at(html)
    return {
        "source_id": raw["source_id"],
        "url": raw["url"],
        "fetched_at": raw["fetched_at"],
        "published_at": published_at.isoformat() if published_at else None,
        "title": extracted.get("title"),
        "text": extracted["text"],
        "checksum": extracted["checksum"],
        "raw_s3_key": raw["raw_bytes_s3_key"],
    }


@celery_app.task(name="tx_news.tasks.pipeline.dedup_store")
def dedup_store(normalized: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    canonical_candidate = sha256_hex(normalized["text"])

    # LSH near-duplicate
    lsh_path = Path("var/lsh_index.pkl")
    lsh = LshDeduper(index=LshIndex(path=lsh_path))
    dup = lsh.find_duplicate(text=normalized["text"])
    if dup and get_article(engine, dup):
        canonical_id = dup
    else:
        canonical_id = canonical_candidate

    # Semantic dedup (only if not already a near-dup)
    embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
    embedder, qdrant_strategy = build_embedder(embedding_cfg)
    model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
    vector = embedder.embed(normalized["text"])
    qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection).resolve_collection_for_embedding(
        vector_size=len(vector),
        model_name_or_path=model_name,
        strategy=qdrant_strategy,
    )
    hits = qdrant.search(vector=vector, limit=1)
    if hits and hits[0].score and hits[0].score >= 0.92:
        canonical_id = scored_point_canonical_id(hits[0]) or str(hits[0].id)

    existing = get_article(engine, canonical_id)
    is_new_canonical = existing is None
    if is_new_canonical:
        article = Article(
            canonical_id=canonical_id,
            title=normalized.get("title"),
            text=normalized["text"],
            checksum=normalized["checksum"],
            lsh_signature=None,
            embedding_model=model_name,
            embedding_dim=len(vector),
            embedding_ref=f"qdrant:{qdrant.collection}:{qdrant.to_point_id(canonical_id)}",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        upsert_article(engine, article)
        # add to LSH index only for newly created canonical articles
        lsh.insert(canonical_id, normalized["text"])
        insert_signal(engine, canonical_id, "breaking", {"reason": "new_canonical"})

    published_at = normalized.get("published_at")
    insert_version(
        engine,
        canonical_id=canonical_id,
        source_id=normalized["source_id"],
        url=normalized["url"],
        fetched_at=datetime.fromisoformat(normalized["fetched_at"]),
        published_at=datetime.fromisoformat(published_at) if published_at else None,
        checksum=normalized["checksum"],
        raw_s3_key=normalized.get("raw_s3_key"),
    )

    # upsert vector for canonical_id (reuse for search + semantic dedup)
    payload = {
        "canonical_id": canonical_id,
        "title": normalized.get("title"),
        "source_id": normalized["source_id"],
        "url": normalized["url"],
        "published_at": published_at,
    }
    qdrant.upsert(point_id=canonical_id, vector=vector, payload=payload)

    return {"canonical_id": canonical_id, "is_new_canonical": is_new_canonical, **normalized}


@celery_app.task(name="tx_news.tasks.pipeline.analyze")
def analyze(canonical: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    canonical_id = str(canonical.get("canonical_id") or "").strip()
    if not canonical_id:
        return {"skipped": True, "reason": "missing_canonical_id"}

    # Cost policy:
    # - GPU mode: prefer local vLLM (llm.deep) for BOTH analyze + deep_analysis; fall back to cloud (llm.chat) on failure.
    # - CPU mode: use cloud (llm.chat) if api_key is configured; otherwise fall back to rules-only.
    accel = (settings.accelerator or "").strip().lower() or "cpu"
    prefer_local = accel != "cpu"
    llm_primary = settings.resolve_llm_deep(file_cfg) if prefer_local else settings.resolve_llm_chat(file_cfg)
    llm_fallback = settings.resolve_llm_chat(file_cfg) if prefer_local else None
    api_key_primary = llm_primary.get("api_key")
    api_key_fallback = (llm_fallback or {}).get("api_key") if llm_fallback else None
    llm_available_now = bool(prefer_local or api_key_primary or api_key_fallback)

    # Fast path: if this canonical text checksum was already analyzed, don't pay the LLM cost again.
    current_checksum = str(canonical.get("checksum") or "").strip()
    existing_analysis = get_analysis(engine, canonical_id)
    if existing_analysis and current_checksum:
        data = existing_analysis.data if isinstance(existing_analysis.data, dict) else {}
        meta = data.get("_txnews") if isinstance(data.get("_txnews"), dict) else {}
        prev_checksum = str(meta.get("text_checksum") or "").strip()
        prev_llm_used = bool(getattr(existing_analysis, "llm_used", 0))
        if prev_checksum and prev_checksum == current_checksum:
            # Idempotency: if checksum unchanged, skip by default.
            # Exception: allow a one-time "upgrade" from rules-only to LLM when LLM becomes available.
            if prev_llm_used or not llm_available_now:
                logger.info("analyze skipped canonical_id=%s reason=same_checksum", canonical_id)
                return {
                    "canonical_id": canonical_id,
                    "skipped": True,
                    "reason": "same_checksum",
                    "llm_used": prev_llm_used,
                }

    # Best-effort lock: avoid duplicate concurrent analyses for the same canonical_id.
    lock_key = f"txnews:lock:analyze:{canonical_id}"
    lock_token = secrets.token_hex(8)
    try:
        redis = Redis.from_url(settings.redis_url)
        got_lock = bool(redis.set(lock_key, lock_token, nx=True, ex=ANALYZE_LOCK_TTL_SECONDS))
    except Exception:
        got_lock = True
        redis = None  # type: ignore[assignment]
    if not got_lock:
        logger.info("analyze skipped canonical_id=%s reason=lock_busy", canonical_id)
        return {"canonical_id": canonical_id, "skipped": True, "reason": "lock_busy"}

    result: dict[str, Any] | None = None
    try:
        text = canonical["text"]
        title = canonical.get("title")

        event_type = classify_event_type(text, title=title)
        planner = EventWindowPlanner(event_windows_minutes=file_cfg.event_windows_minutes or {})

        published_at = canonical.get("published_at")
        dt = datetime.fromisoformat(published_at) if published_at else utcnow()
        window_start = planner.window_start(event_type=event_type, dt=dt)

        name_map = load_a_share_name_map(engine)
        tickers = TickerMatcher(name_map=name_map).match(f"{title or ''}\n{text}")
        entities: list[dict[str, Any]] = tickers.copy()

        key = pick_key_entity(entities)
        event_id = stable_event_id(event_type, key=key, window_start_iso=window_start.isoformat())

        result: dict[str, Any] = {
            "canonical_id": canonical_id,
            "event_id": event_id,
            "event_type": event_type,
            "entities": entities,
            "tickers": tickers,
            "impact": {"scope": "index", "direction": "uncertain", "confidence": 0.3},
            "index_view": {"direction": "uncertain", "drivers": [], "risks": []},
            "evidence": [{"url": canonical["url"], "source_id": canonical["source_id"]}],
        }

        llm_used = False
        system = (
            "你是金融新闻分析助手。请只输出一个 JSON 对象，不要输出任何多余文本。"
            "输出字段：event_type, entities, tickers, impact, index_view, evidence。"
            "注意：不要输出新闻全文，不要输出长段引用。"
        )
        user = (
            f"标题：{title or ''}\n"
            f"正文：{text[:6000]}\n\n"
            f"已识别个股候选：{tickers}\n"
            "请基于以上内容进行结构化标注与推理，给出对大盘与个股的方向性判断（情景化）。"
        )

        def _try_llm(llm_cfg: dict[str, Any], api_key: Any) -> bool:
            nonlocal llm_used
            client = DashScopeClient(
                api_key=str(api_key) if api_key else None,
                model=str(llm_cfg.get("model") or "qwen3-max"),
                base_url=str(llm_cfg.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                timeout_seconds=int(llm_cfg.get("timeout_seconds") or 60),
            )
            out = client.chat_json(system=system, user=user)
            for k in ("event_type", "entities", "tickers", "impact", "index_view", "evidence"):
                if k in out:
                    result[k] = out[k]
            llm_used = True
            return True

        # Primary: local vLLM in GPU mode; cloud in CPU mode.
        # For local vLLM, api_key can be empty; for cloud fallback, api_key is required.
        try_primary = prefer_local or bool(api_key_primary)
        if try_primary:
            try:
                _try_llm(dict(llm_primary or {}), api_key_primary)
            except Exception as e:
                logger.warning("primary llm failed; try fallback if available: %s", e)
                if llm_fallback and api_key_fallback:
                    try:
                        _try_llm(dict(llm_fallback or {}), api_key_fallback)
                    except Exception as e2:
                        logger.warning("fallback llm failed; fall back to rules: %s", e2)

        result["tickers"] = _normalize_tickers(result.get("tickers"))

        result["_txnews"] = {
            "text_checksum": current_checksum or None,
            "source_id": canonical.get("source_id"),
            "url": canonical.get("url"),
            "published_at": canonical.get("published_at"),
            "fetched_at": canonical.get("fetched_at"),
            "analyzed_at": utcnow().isoformat(),
        }

        upsert_analysis(engine, canonical_id, event_type=str(result.get("event_type", event_type)), data=result, llm_used=llm_used)
        insert_signal(engine, canonical_id, "analysis_updated", {"event_type": result.get("event_type", event_type), "event_id": event_id})
    finally:
        if got_lock and redis is not None:
            try:
                _release_redis_lock(redis, lock_key, lock_token)
            except Exception:
                pass

    # Deep Path: only for newly created canonical articles, and only if deep LLM is available.
    if canonical.get("is_new_canonical"):
        deep_llm = settings.resolve_llm_deep(file_cfg)
        deep_base_url = str(deep_llm.get("base_url") or "")
        deep_api_key = deep_llm.get("api_key")
        is_local = _is_local_llm_base_url(deep_base_url)
        if deep_api_key or is_local:
            deep_optimize.delay(canonical)

    return result or {"canonical_id": canonical_id, "skipped": True, "reason": "analysis_failed"}


@celery_app.task(name="tx_news.tasks.pipeline.ingest_raw")
def ingest_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Entry task used by NATS bridge."""
    flow = chain(
        normalize_raw.s(raw),
        dedup_store.s(),
        analyze.s(),
    )
    async_result = flow.apply_async()
    return {"task_id": async_result.id}
