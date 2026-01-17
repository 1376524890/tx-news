# Input: Settings(redis_url) 与任务模块列表
# Output: celery_app（含路由/队列/注册策略）
# Pos: Celery 应用配置入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from tx_news.settings import get_settings


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
        ],
    )
    app.conf.task_default_queue = "default"
    app.conf.task_routes = {
        "tx_news.tasks.pipeline.*": {"queue": "default"},
        "tx_news.tasks.deep_analysis.*": {"queue": "default"},
        "tx_news.tasks.maintenance.*": {"queue": "default"},
        "tx_news.tasks.kg.*": {"queue": "default"},
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
    }
    return app


celery_app = make_celery()

# Ensure task modules are imported so their `@celery_app.task` decorators execute and
# register tasks even if Celery's loader/import hooks behave differently across envs.
# (This prevents "Received unregistered task ..." when the worker starts.)
from tx_news.tasks import deep_analysis as _deep_analysis  # noqa: E402,F401
from tx_news.tasks import kg as _kg  # noqa: E402,F401
from tx_news.tasks import maintenance as _maintenance  # noqa: E402,F401
from tx_news.tasks import pipeline as _pipeline  # noqa: E402,F401
