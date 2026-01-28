# Input: queue worker dependencies (DB + pipeline.analyze) mocked
# Output: process_queue_worker emits canonical payload that triggers deep analysis
# Pos: news_queue regression test (change with FOLDER.md)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
import sys
import types


@dataclass
class FakeArticle:
    canonical_id: str
    title: str
    text: str
    checksum: str


@dataclass
class FakeVersion:
    url: str
    source_id: str
    published_at: datetime | None
    fetched_at: datetime | None


def test_process_queue_worker_marks_new_for_deep(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    class DummyAnalyze:
        def delay(self, canonical: dict) -> None:
            captured["canonical"] = canonical

    if "tx_news.tasks.celery_app" not in sys.modules:
        celery_app_mod = types.ModuleType("tx_news.tasks.celery_app")

        class DummyCeleryApp:
            def task(self, *_args, **_kwargs):
                def decorator(func):
                    return func

                return decorator

        celery_app_mod.celery_app = DummyCeleryApp()
        sys.modules["tx_news.tasks.celery_app"] = celery_app_mod

    if "tx_news.tasks.pipeline" not in sys.modules:
        pipeline_mod = types.ModuleType("tx_news.tasks.pipeline")
        pipeline_mod.analyze = DummyAnalyze()
        sys.modules["tx_news.tasks.pipeline"] = pipeline_mod

    from tx_news.tasks import news_queue as news_queue_mod
    import tx_news.settings as settings_mod
    import tx_news.storage.postgres as pg_mod

    now = datetime.now(timezone.utc)
    article = FakeArticle(
        canonical_id="cid-1",
        title="hello",
        text="body",
        checksum="checksum-1",
    )
    version = FakeVersion(
        url="https://example.com",
        source_id="source-1",
        published_at=now,
        fetched_at=now,
    )

    monkeypatch.setattr(settings_mod, "get_settings", lambda: SimpleNamespace(pg_dsn="postgresql://ignored"))
    monkeypatch.setattr(pg_mod, "make_engine", lambda _dsn: object())
    monkeypatch.setattr(pg_mod, "init_db", lambda _engine: None)
    monkeypatch.setattr(pg_mod, "get_article", lambda _engine, _cid: article)
    monkeypatch.setattr(pg_mod, "get_latest_version", lambda _engine, _cid: version)

    result = news_queue_mod.process_queue_worker("cid-1")

    assert result["triggered"] is True
    assert captured["canonical"]["is_new_canonical"] is True
