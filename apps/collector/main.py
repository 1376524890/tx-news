from __future__ import annotations

import time

from tx_news.crawler.collector import Collector
from tx_news.crawler.fetcher import Fetcher
from tx_news.logging import configure_logging
from tx_news.settings import get_settings
from tx_news.storage.minio import S3Client


def main() -> None:
    configure_logging()
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    sources = settings.load_sources()

    crawler_cfg = file_cfg.crawler or {}
    max_concurrency = int(crawler_cfg.get("max_concurrency", 1))
    timeout_seconds = int(crawler_cfg.get("request_timeout_seconds", 20))
    max_retries = int(crawler_cfg.get("max_retries", 3))

    s3 = S3Client(
        endpoint_url=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
    )

    collector = Collector(
        engine_dsn=settings.pg_dsn,
        nats_url=settings.nats_url,
        nats_stream=settings.nats_stream,
        s3=s3,
        fetcher=Fetcher(timeout_seconds=timeout_seconds, max_retries=max_retries),
        max_concurrency=max_concurrency,
    )

    # v0: simple loop; run once per minute
    while True:
        collector.run_once(sources)
        time.sleep(60)


if __name__ == "__main__":
    main()

