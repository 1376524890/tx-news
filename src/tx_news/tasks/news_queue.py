# Input: canonical articles with published_at/fetched_at + queue worker signals
# Output: Priority queue + Celery queue worker processing results (queue_worker_task; 24h expiry + timeout guard)
# Pos: News priority queue + worker task registration (change with FOLDER.md)

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from redis import Redis

from tx_news.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

QUEUE_KEY = "txnews:analysis_queue"
QUEUE_SCORE_KEY = "txnews:analysis_queue:scores"

DEFAULT_QUEUE_TTL_HOURS = 24


def _get_redis() -> Redis | None:
    try:
        from tx_news.settings import get_settings

        settings = get_settings()
        return Redis.from_url(settings.redis_url)
    except Exception:
        return None


def add_to_queue(
    canonical_id: str,
    published_at: datetime | None = None,
    fetched_at: datetime | None = None,
    priority: float = 0.0,
) -> bool:
    """Add article to analysis priority queue. Score is timestamp (newer first)."""
    redis = _get_redis()
    if not redis:
        return False
    
    try:
        utc8_tz = timezone(timedelta(hours=8))
        now = datetime.now(utc8_tz)
        
        # Use published_at if available, otherwise fetched_at, otherwise now
        ref_time = published_at or fetched_at or now
        
        # Ensure timezone
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)
        
        # Convert to UTC+8 for comparison
        ref_time_utc8 = ref_time.astimezone(utc8_tz)
        
        # Score: timestamp so newer articles have higher score (higher priority)
        score = ref_time_utc8.timestamp()
        
        # Use provided priority to adjust score (higher priority value = higher priority)
        score += priority
        
        pipeline = redis.pipeline()
        pipeline.zadd(QUEUE_KEY, {canonical_id: score})
        pipeline.hset(QUEUE_SCORE_KEY, canonical_id, score)
        pipeline.expire(QUEUE_KEY, DEFAULT_QUEUE_TTL_HOURS * 3600)
        pipeline.expire(QUEUE_SCORE_KEY, DEFAULT_QUEUE_TTL_HOURS * 3600)
        pipeline.execute()
        
        logger.debug("added to queue canonical_id=%s score=%s ref_time=%s", canonical_id, score, ref_time_utc8)
        return True
    except Exception as e:
        logger.warning("failed to add to queue canonical_id=%s err=%s", canonical_id, e)
        return False


def get_next_from_queue(block: bool = True, timeout: int = 5) -> str | None:
    """Get next article from queue (newest first). Returns canonical_id or None."""
    redis = _get_redis()
    if not redis:
        return None

    try:
        if block:
            # Blocking: wait for new item or timeout
            # bzpopmax returns (queue_name, member, score) or None
            result = redis.bzpopmax(QUEUE_KEY, timeout=timeout)
            if result:
                queue_name, member, score = result
                canonical_id = member
                if isinstance(canonical_id, bytes):
                    canonical_id = canonical_id.decode("utf-8")
                redis.hdel(QUEUE_SCORE_KEY, canonical_id)
                logger.info("dequeued canonical_id=%s score=%s", canonical_id, score)
                return canonical_id
        else:
            # Non-blocking: get top item
            # zpopmax returns [(member, score)] or []
            result = redis.zpopmax(QUEUE_KEY)
            if result and result[0]:
                canonical_id, score = result[0]
                if isinstance(canonical_id, bytes):
                    canonical_id = canonical_id.decode("utf-8")
                redis.hdel(QUEUE_SCORE_KEY, canonical_id)
                logger.info("dequeued canonical_id=%s score=%s", canonical_id, score)
                return canonical_id

        return None
    except Exception as e:
        logger.warning("failed to get from queue err=%s", e)
        return None


def remove_from_queue(canonical_id: str) -> bool:
    """Remove article from queue."""
    redis = _get_redis()
    if not redis:
        return False

    try:
        pipeline = redis.pipeline()
        pipeline.zrem(QUEUE_KEY, canonical_id)
        pipeline.hdel(QUEUE_SCORE_KEY, canonical_id)
        pipeline.execute()
        logger.debug("removed from queue canonical_id=%s", canonical_id)
        return True
    except Exception as e:
        logger.warning("failed to remove from queue canonical_id=%s err=%s", canonical_id, e)
        return False


def remove_expired_articles(hours: int = 24) -> int:
    """Remove expired articles from queue (>N hours old). Returns count of removed articles."""
    redis = _get_redis()
    if not redis:
        return 0
    
    try:
        utc8_tz = timezone(timedelta(hours=8))
        now = datetime.now(utc8_tz)
        cutoff_time = now - timedelta(hours=hours)
        cutoff_ts = cutoff_time.timestamp()
        
        # Get all expired articles (score < cutoff_ts, i.e., older than cutoff)
        # Since scores are timestamps, older articles have smaller scores
        expired = redis.zrangebyscore(QUEUE_KEY, "-inf", f"({cutoff_ts}")
        
        if not expired:
            return 0
        
        # Remove expired articles
        canonical_ids = [item.decode("utf-8") if isinstance(item, bytes) else item for item in expired]
        if canonical_ids:
            pipeline = redis.pipeline()
            pipeline.zrem(QUEUE_KEY, *canonical_ids)
            pipeline.hdel(QUEUE_SCORE_KEY, *canonical_ids)
            pipeline.execute()
            
            logger.info("removed expired articles from queue count=%s cutoff_time=%s", len(canonical_ids), cutoff_time)
            return len(canonical_ids)
        
        return 0
    except Exception as e:
        logger.warning("failed to remove expired articles err=%s", e)
        return 0


def get_queue_size() -> int:
    """Get current queue size."""
    redis = _get_redis()
    if not redis:
        return 0

    try:
        return redis.zcard(QUEUE_KEY)
    except Exception:
        return 0


def is_in_queue(canonical_id: str) -> bool:
    """Check if article is in queue."""
    redis = _get_redis()
    if not redis:
        return False

    try:
        return redis.hexists(QUEUE_SCORE_KEY, canonical_id)
    except Exception:
        return False


def _is_article_expired(published_at: datetime | None, fetched_at: datetime | None, hours: int = 24) -> bool:
    """Check if article is expired based on published_at or fetched_at (UTC+8)."""
    from datetime import timedelta

    try:
        utc8_tz = timezone(timedelta(hours=8))
        now = datetime.now(utc8_tz)
        cutoff_ts = (now - timedelta(hours=hours)).timestamp()

        if published_at:
            dt = published_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
            if ts < cutoff_ts:
                return True

        if fetched_at:
            dt = fetched_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
            if ts < cutoff_ts:
                return True
    except Exception:
        pass
    return False


def process_queue_worker(canonical_id: str) -> dict:
    """
    Process article from queue.
    Called by queue worker after dequeuing.
    Checks for >24h expiration and triggers analyze.
    """
    from tx_news.storage.postgres import get_article, init_db, make_engine
    from tx_news.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    article = get_article(engine, canonical_id)
    if not article:
        logger.warning("article not found canonical_id=%s", canonical_id)
        return {"skipped": True, "reason": "article_not_found"}

    # Get published_at and fetched_at from latest version
    from tx_news.storage.postgres import get_latest_version
    v = get_latest_version(engine, canonical_id)
    if not v:
        logger.warning("version not found canonical_id=%s", canonical_id)
        return {"skipped": True, "reason": "version_not_found"}

    published_at = v.published_at
    fetched_at = v.fetched_at

    # Check for >24h expiration
    if _is_article_expired(published_at, fetched_at, hours=24):
        logger.info("queue worker skipped canonical_id=%s reason=article_expired", canonical_id)
        return {"skipped": True, "reason": "article_expired"}

    # Import here to avoid circular dependency
    canonical_dict = {
        "canonical_id": canonical_id,
        "title": article.title,
        "text": article.text,
        "checksum": article.checksum,
        "url": v.url,
        "source_id": v.source_id,
        "published_at": published_at.isoformat() if published_at else None,
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        # Queue only stores newly created canonicals; keep flag True so deep analysis is eligible.
        "is_new_canonical": True,
    }

    # Trigger analyze task
    from tx_news.tasks.pipeline import analyze

    try:
        analyze.delay(canonical_dict)
        logger.info("triggered analyze canonical_id=%s", canonical_id)
        return {"triggered": True, "canonical_id": canonical_id}
    except Exception as e:
        logger.warning("failed to trigger analyze canonical_id=%s err=%s", canonical_id, e)
        return {"skipped": True, "reason": "analyze_failed", "error": str(e)}


def run_queue_worker_loop(
    max_iterations: int | None = None,
    block_timeout: int = 5,
    max_runtime_seconds: int | None = 15,
) -> None:
    """
    Run queue worker loop.
    Continuously dequeues and processes articles from the priority queue.
    """
    start_ts = datetime.now(timezone.utc)
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        if max_runtime_seconds is not None:
            elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()
            if elapsed >= max_runtime_seconds:
                logger.info("queue worker reached max_runtime_seconds=%s", max_runtime_seconds)
                break
        iteration += 1

        # Remove expired articles before getting next
        remove_expired_articles(hours=24)

        # Get next article from queue (blocking only when timeout > 0)
        use_block = block_timeout > 0
        canonical_id = get_next_from_queue(block=use_block, timeout=block_timeout)

        if canonical_id:
            logger.info("processing queue iteration=%s canonical_id=%s", iteration, canonical_id)
            result = process_queue_worker(canonical_id)
            logger.info("queue processing result=%s", result)
        else:
            # Queue is empty or timeout
            logger.debug("queue empty, waiting... iteration=%s", iteration)
            if not use_block:
                # Non-blocking mode: exit early to avoid busy loops.
                break

        # If queue is empty and we have removed expired items, continue waiting
        # Otherwise, keep running until max_iterations
        if max_iterations is not None and iteration >= max_iterations:
            logger.info("queue worker reached max_iterations=%s", max_iterations)
            break


@celery_app.task(name="tx_news.tasks.news_queue.queue_worker_task")
def queue_worker_task(max_iterations: int = 100, block_timeout: int = 0, max_runtime_seconds: int = 15) -> dict:
    """
    Celery task to run queue worker.
    Processes max_iterations articles from the queue.
    Designed to be called periodically (e.g., every minute).
    """
    logger.info(
        "queue worker started max_iterations=%s block_timeout=%s max_runtime_seconds=%s",
        max_iterations,
        block_timeout,
        max_runtime_seconds,
    )

    initial_size = get_queue_size()
    logger.info("queue worker initial queue_size=%s", initial_size)

    run_queue_worker_loop(
        max_iterations=max_iterations,
        block_timeout=block_timeout,
        max_runtime_seconds=max_runtime_seconds,
    )

    final_size = get_queue_size()
    logger.info("queue worker finished processed=%s remaining=%s", initial_size - final_size, final_size)

    return {
        "initial_size": initial_size,
        "final_size": final_size,
        "processed": initial_size - final_size,
    }
