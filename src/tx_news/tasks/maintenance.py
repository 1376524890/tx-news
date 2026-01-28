# Input: tushare.token/网络/缓存 + retention.raw_days + MinIO/Postgres 连接
# Output: A 股主数据入库、raw TTL 清理结果
# Pos: 维护任务集合（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from tx_news.db import RawDoc
from tx_news.integrations.tushare_sync import TushareSync
from tx_news.settings import get_settings
from tx_news.storage.minio import S3Client
from tx_news.storage.postgres import (
    get_articles,
    get_latest_version,
    init_db,
    make_engine,
    session_scope,
    upsert_a_share_basic,
)
from tx_news.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@celery_app.task(name="tx_news.tasks.maintenance.sync_tushare")
def sync_tushare() -> dict:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    token = (file_cfg.tushare or {}).get("token") or ""
    ttl_hours = int((file_cfg.tushare or {}).get("cache_ttl_hours", 12))

    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    sync = TushareSync(token=token)

    # Stock master data is slow-moving; prefer local cache if fresh.
    ttl_seconds = max(1, ttl_hours) * 3600
    if sync.cache_is_fresh(ttl_seconds=ttl_seconds):
        cached = sync.load_cached_stock_basic() or []
        if cached:
            upsert_a_share_basic(engine, cached)
            return {"rows": len(cached), "source": "cache_fresh", "ttl_hours": ttl_hours}

    source = "tushare"
    try:
        # prefer tushare for richer fields; can fail due to rate-limit/network
        if token.strip():
            rows = sync.fetch_stock_basic_tushare()
        else:
            raise RuntimeError("tushare.token is empty; skip tushare")
    except Exception as e:
        logger.warning("tushare fetch failed; try akshare then local cache: %s", e)
        try:
            rows = sync.fetch_stock_basic_akshare()
            source = "akshare"
        except Exception as e2:
            logger.warning("akshare fetch failed; falling back to local cache: %s", e2)
            cached = sync.load_cached_stock_basic()
            if not cached:
                raise
            rows = cached
            source = "cache"
    upsert_a_share_basic(engine, rows)
    return {"rows": len(rows), "source": source, "ttl_hours": ttl_hours}


@celery_app.task(name="tx_news.tasks.maintenance.cleanup_raw")
def cleanup_raw() -> dict:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    days = int((file_cfg.retention or {}).get("raw_days", 7))
    cutoff = utcnow() - timedelta(days=days)

    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    s3 = S3Client(
        endpoint_url=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
    )

    deleted = 0
    with session_scope(engine) as s:
        rows = s.scalars(select(RawDoc).where(RawDoc.created_at < cutoff)).all()
        for r in rows:
            if r.s3_key:
                try:
                    s3.delete_key(r.s3_key)
                except Exception as e:
                    logger.warning("minio delete failed key=%s err=%s", r.s3_key, e)
            deleted += 1
        s.execute(delete(RawDoc).where(RawDoc.created_at < cutoff))

    return {"deleted_raw_docs": deleted, "cutoff": cutoff.isoformat()}


@celery_app.task(name="tx_news.tasks.maintenance.analyze_recent_articles")
def analyze_recent_articles() -> dict[str, Any]:
    """
    Periodically analyze recent articles that may have been missed by the NATS pipeline.
    This ensures articles get analyzed even if NATS bridge is not working.
    """
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    cutoff_hours = int((file_cfg.maintenance or {}).get("analyze_cutoff_hours", 24))
    cutoff = utcnow() - timedelta(hours=cutoff_hours)

    with session_scope(engine) as s:
        from sqlalchemy import select
        from tx_news.db import Article

        articles = s.scalars(
            select(Article)
            .where(Article.created_at >= cutoff)
            .order_by(Article.created_at)
            .limit(100)
        ).all()

    analyzed = 0
    skipped = 0
    for article in articles:
        v = get_latest_version(engine, article.canonical_id)
        fetched_at = (v.fetched_at if v else None) or article.created_at
        published_at = (v.published_at if v else None) or article.created_at
        canonical = {
            "canonical_id": article.canonical_id,
            "source_id": v.source_id if v else None,
            "url": v.url if v else None,
            "fetched_at": fetched_at.isoformat(),
            "published_at": published_at.isoformat(),
            "checksum": article.checksum,
            "is_new_canonical": False,
        }
        try:
            celery_app.send_task("tx_news.tasks.pipeline.analyze", args=[canonical])
            analyzed += 1
        except Exception as e:
            logger.warning("failed to enqueue analysis for canonical_id=%s err=%s", article.canonical_id, e)
            skipped += 1

    logger.info(
        "analyze_recent_articles completed: articles=%d analyzed=%d skipped=%d cutoff_hours=%d",
        len(articles),
        analyzed,
        skipped,
        cutoff_hours,
    )
    return {
        "total_articles": len(articles),
        "analyzed": analyzed,
        "skipped": skipped,
        "cutoff_hours": cutoff_hours,
    }
