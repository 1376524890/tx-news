from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from tx_news.db import RawDoc
from tx_news.integrations.tushare_sync import TushareSync
from tx_news.settings import get_settings
from tx_news.storage.minio import S3Client
from tx_news.storage.postgres import init_db, make_engine, session_scope, upsert_a_share_basic
from tx_news.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@celery_app.task(name="tx_news.tasks.maintenance.sync_tushare")
def sync_tushare() -> dict:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    token = (file_cfg.tushare or {}).get("token") or ""
    if not token.strip():
        raise RuntimeError("tushare.token is empty in config/config.yaml")

    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    rows = TushareSync(token=token).fetch_stock_basic()
    upsert_a_share_basic(engine, rows)
    return {"rows": len(rows)}


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

