# Input: Collector/Fetcher 与外部依赖的 fakes/monkeypatch
# Output: 采集器异常场景下不中断的断言
# Pos: crawler 采集器单元测试（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FakeFetchResult:
    url: str
    status_code: int = 200
    body: bytes = b"<html></html>"
    fetched_at: datetime = field(default_factory=utcnow)
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str | None = "text/html"
    checksum: str = "x"


def test_process_root_handles_root_fetch_exception(caplog: pytest.LogCaptureFixture) -> None:
    from tx_news.crawler.collector import Collector

    @dataclass(frozen=True)
    class FakeFetcher:
        def fetch(self, url: str):
            raise TimeoutError("boom")

    collector = Collector(
        engine_dsn="postgresql://ignored",
        nats_url="nats://ignored",
        nats_stream="txnews",
        s3=object(),  # unused
        fetcher=FakeFetcher(),
    )

    collector._process_root(engine=None, bus=object(), root_url="https://example.com/")  # type: ignore[arg-type]
    assert "root fetch failed" in caplog.text


def test_process_root_handles_extract_links_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import tx_news.crawler.collector as collector_mod
    from tx_news.crawler.collector import Collector

    @dataclass(frozen=True)
    class FakeFetcher:
        def fetch(self, url: str):
            return FakeFetchResult(url=url, status_code=200, body=b"<html></html>")

    def boom(*_args, **_kwargs):
        raise ValueError("bad html")

    monkeypatch.setattr(collector_mod, "extract_links", boom)

    collector = Collector(
        engine_dsn="postgresql://ignored",
        nats_url="nats://ignored",
        nats_stream="txnews",
        s3=object(),  # unused
        fetcher=FakeFetcher(),
    )

    collector._process_root(engine=None, bus=object(), root_url="https://example.com/")  # type: ignore[arg-type]
    assert "extract_links failed" in caplog.text


def test_run_once_handles_nats_ensure_stream_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import tx_news.crawler.collector as collector_mod
    from tx_news.crawler.collector import Collector

    monkeypatch.setattr(collector_mod, "make_engine", lambda _dsn: object())
    monkeypatch.setattr(collector_mod, "init_db", lambda _engine: None)
    monkeypatch.setattr(collector_mod, "ensure_sources", lambda _engine, _root_urls: None)

    class FakeBus:
        def __init__(self, url: str, stream: str) -> None:
            self.url = url
            self.stream = stream

        async def ensure_stream(self) -> None:
            raise RuntimeError("nats down")

    monkeypatch.setattr(collector_mod, "NatsBus", FakeBus)

    collector = Collector(
        engine_dsn="postgresql://ignored",
        nats_url="nats://ignored",
        nats_stream="txnews",
        s3=object(),  # unused
        fetcher=object(),  # unused
    )

    collector.run_once(["https://example.com/"])
    assert "collector nats ensure_stream failed" in caplog.text
