# Input: NATS raw payload + Postgres/MinIO/Qdrant/embedding/LLM（可选）
# Output: canonical 入库、向量 upsert、analysis upsert、signals 写入
# Pos: 主流水线任务定义（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery import chain

from tx_news.analysis.dashscope import DashScopeClient
from tx_news.analysis.rules import EventWindowPlanner, classify_event_type, pick_key_entity, stable_event_id
from tx_news.analysis.tickers import TickerMatcher
from tx_news.db import Article
from tx_news.dedup.lsh import LshDeduper, LshIndex
from tx_news.embedding.embedder import Embedder
from tx_news.normalize.metadata import extract_published_at
from tx_news.normalize.readability import ReadabilityExtractor
from tx_news.settings import get_settings
from tx_news.storage.minio import S3Client
from tx_news.storage.postgres import (
    get_article,
    init_db,
    insert_signal,
    insert_version,
    load_a_share_name_map,
    make_engine,
    upsert_analysis,
    upsert_article,
)
from tx_news.storage.qdrant import QdrantStore
from tx_news.tasks.celery_app import celery_app
from tx_news.tasks.deep_analysis import deep_optimize

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    embedding_cfg = file_cfg.embedding or {}
    model_name = embedding_cfg.get("model_name", "BAAI/bge-small-zh-v1.5")
    qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection)
    embedder = Embedder(model_name_or_path=model_name)
    vector = embedder.embed(normalized["text"][:4000])
    hits = qdrant.search(vector=vector, limit=1)
    if hits and hits[0].score and hits[0].score >= 0.92:
        canonical_id = str(hits[0].id)

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
            embedding_ref=f"qdrant:{settings.qdrant_collection}:{canonical_id}",
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
        "canonical_id": canonical["canonical_id"],
        "event_id": event_id,
        "event_type": event_type,
        "entities": entities,
        "tickers": tickers,
        "impact": {"scope": "index", "direction": "uncertain", "confidence": 0.3},
        "index_view": {"direction": "uncertain", "drivers": [], "risks": []},
        "evidence": [{"url": canonical["url"], "source_id": canonical["source_id"]}],
    }

    llm_used = False
    api_key = settings.dashscope_api_key or ((file_cfg.llm or {}).get("api_key"))
    if api_key:
        llm_used = True
        client = DashScopeClient(api_key=api_key, model=(file_cfg.llm or {}).get("model", "qwen3-max"))
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
        try:
            out = client.chat_json(system=system, user=user)
            # merge with required ids
            for k in ("event_type", "entities", "tickers", "impact", "index_view", "evidence"):
                if k in out:
                    result[k] = out[k]
        except Exception as e:
            llm_used = False
            logger.warning("dashscope llm failed; fall back to rules: %s", e)

    upsert_analysis(engine, canonical["canonical_id"], event_type=str(result.get("event_type", event_type)), data=result, llm_used=llm_used)
    insert_signal(engine, canonical["canonical_id"], "analysis_updated", {"event_type": result.get("event_type", event_type), "event_id": event_id})

    # Deep Path: only for newly created canonical articles, and only if LLM is available.
    if canonical.get("is_new_canonical") and api_key:
        deep_optimize.delay(canonical)

    return result


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
