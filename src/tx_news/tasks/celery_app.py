from __future__ import annotations

from celery import Celery

from tx_news.settings import get_settings


def make_celery() -> Celery:
    settings = get_settings()
    app = Celery("tx_news", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.task_default_queue = "default"
    app.conf.task_routes = {
        "tx_news.tasks.pipeline.*": {"queue": "default"},
    }
    app.conf.worker_prefetch_multiplier = 1
    app.conf.task_acks_late = True
    return app


celery_app = make_celery()

