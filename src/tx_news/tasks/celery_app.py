from __future__ import annotations

from celery import Celery

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
        ],
    )
    app.conf.task_default_queue = "default"
    app.conf.task_routes = {
        "tx_news.tasks.pipeline.*": {"queue": "default"},
        "tx_news.tasks.deep_analysis.*": {"queue": "default"},
        "tx_news.tasks.maintenance.*": {"queue": "default"},
    }
    app.conf.worker_prefetch_multiplier = 1
    app.conf.task_acks_late = True
    return app


celery_app = make_celery()

# Ensure task modules are imported so their `@celery_app.task` decorators execute and
# register tasks even if Celery's loader/import hooks behave differently across envs.
# (This prevents "Received unregistered task ..." when the worker starts.)
from tx_news.tasks import deep_analysis as _deep_analysis  # noqa: E402,F401
from tx_news.tasks import maintenance as _maintenance  # noqa: E402,F401
from tx_news.tasks import pipeline as _pipeline  # noqa: E402,F401
