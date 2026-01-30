# Input: Settings(redis_url) 与任务模块列表
# Output: celery_app（含路由/队列/注册策略/任务超时保护与显式 routing_key）
# Pos: Celery 应用配置入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from tx_news.settings import get_settings


def _configure_worker_logging() -> None:
    """
    Silence noisy HTTP request logs (httpx/httpcore/qdrant) while keeping INFO logs for tasks.
    Celery's -l INFO should not include per-request transport logs.
    """
    for name in ("httpx", "httpcore", "qdrant_client", "qdrant_client.http"):
        logging.getLogger(name).setLevel(logging.WARNING)


def make_celery() -> Celery:
    settings = get_settings()
    # `include` is required so the worker registers tasks when started via:
    # `celery -A tx_news.tasks.celery_app.celery_app worker ...`
    app = Celery(
        "tx_news",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=[
            "tx_news.tasks.pipeline",
            "tx_news.tasks.deep_analysis",
            "tx_news.tasks.maintenance",
            "tx_news.tasks.kg",
            "tx_news.tasks.causal",
            "tx_news.tasks.news_queue",
        ],
    )
    app.conf.task_default_queue = "default"
    app.conf.task_default_exchange = "default"
    app.conf.task_default_exchange_type = "direct"
    app.conf.task_default_routing_key = "default"
    app.conf.task_queues = (
        Queue("default", routing_key="default"),
        Queue("analysis", routing_key="analysis"),
    )
    app.conf.task_routes = {
        "tx_news.tasks.pipeline.ingest_raw": {"queue": "default", "routing_key": "default"},
        "tx_news.tasks.pipeline.normalize_raw": {"queue": "default", "routing_key": "default"},
        "tx_news.tasks.pipeline.dedup_store": {"queue": "default", "routing_key": "default"},
        "tx_news.tasks.pipeline.analyze": {"queue": "analysis", "routing_key": "analysis"},
        "tx_news.tasks.deep_analysis.*": {"queue": "analysis", "routing_key": "analysis"},
        "tx_news.tasks.maintenance.*": {"queue": "default", "routing_key": "default"},
        "tx_news.tasks.kg.*": {"queue": "default", "routing_key": "default"},
        "tx_news.tasks.causal.*": {"queue": "default", "routing_key": "default"},
        "tx_news.tasks.news_queue.queue_worker_task": {"queue": "analysis", "routing_key": "analysis"},
        "tx_news.tasks.news_queue.*": {"queue": "default", "routing_key": "default"},
    }
    # Avoid long-running queue worker tasks blocking the single-worker lane.
    # Soft time limit raises SoftTimeLimitExceeded for graceful cleanup,
    # hard time limit kills the task if it fails to stop.
    app.conf.task_annotations = {
        "tx_news.tasks.news_queue.queue_worker_task": {
            "soft_time_limit": 20,
            "time_limit": 30,
        }
    }
    app.conf.worker_prefetch_multiplier = 1
    app.conf.task_acks_late = True

    # Periodic schedules required by v2.2 (implemented as a separate celery-beat service in compose).
    # Keep schedules deterministic and low-frequency to avoid stressing Qdrant/Postgres.
    app.conf.beat_schedule = {
        "kg_gc_hourly": {
            "task": "tx_news.tasks.kg.kg_gc",
            "schedule": crontab(minute=5),  # every hour at :05
        },
        "kg_reconcile_daily": {
            "task": "tx_news.tasks.kg.kg_reconcile",
            "schedule": crontab(minute=15, hour=3),  # daily at 03:15 (UTC)
        },
        "analyze_recent_articles_hourly": {
            "task": "tx_news.tasks.maintenance.analyze_recent_articles",
            "schedule": crontab(minute=0),  # every hour
        },
        "news_queue_worker_every_minute": {
            "task": "tx_news.tasks.news_queue.queue_worker_task",
            "schedule": crontab(minute="*"),  # every minute
        },
    }
    return app


_configure_worker_logging()
celery_app = make_celery()

# Ensure task modules are imported so their `@celery_app.task` decorators execute and
# register tasks even if Celery's loader/import hooks behave differently across envs.
# (This prevents "Received unregistered task ..." when the worker starts.)
from tx_news.tasks import deep_analysis as _deep_analysis  # noqa: E402,F401
from tx_news.tasks import kg as _kg  # noqa: E402,F401
from tx_news.tasks import causal as _causal  # noqa: E402,F401
from tx_news.tasks import maintenance as _maintenance  # noqa: E402,F401
from tx_news.tasks import news_queue as _news_queue  # noqa: E402,F401
from tx_news.tasks import pipeline as _pipeline  # noqa: E402,F401
