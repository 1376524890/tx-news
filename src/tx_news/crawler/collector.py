# Input: 来源 root_urls、Fetcher、S3Client、Postgres DSN、NATS URL
# Output: raw 内容写入 MinIO、元数据写入 Postgres、并发布 NATS raw 消息
# Pos: 采集编排器（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from tx_news.bus.nats import NatsBus
from tx_news.crawler.fetcher import Fetcher, extract_links
from tx_news.storage.minio import S3Client
from tx_news.storage.postgres import ensure_sources, init_db, make_engine, upsert_raw
from tx_news.storage.postgres import existing_raw_urls

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Collector:
    engine_dsn: str
    nats_url: str
    nats_stream: str
    s3: S3Client
    fetcher: Fetcher
    max_concurrency: int = 1

    def run_once(self, root_urls: list[str]) -> None:
        engine = make_engine(self.engine_dsn)
        init_db(engine)
        ensure_sources(engine, root_urls)

        bus = NatsBus(url=self.nats_url, stream=self.nats_stream)
        asyncio.run(bus.ensure_stream())

        for root_url in root_urls:
            self._process_root(engine, bus, root_url)

    def _process_root(self, engine, bus: NatsBus, root_url: str) -> None:
        source_id = root_url.replace("https://", "").replace("http://", "").strip("/").lower()
        logger.info("fetch root source_id=%s url=%s", source_id, root_url)
        root = self.fetcher.fetch(root_url)
        if root.status_code >= 400:
            logger.warning("root fetch non-2xx status=%s url=%s", root.status_code, root_url)
            return

        links = extract_links(root.url, root.body, max_links=100)
        logger.info("root links source_id=%s count=%s", source_id, len(links))

        already = existing_raw_urls(engine, links)
        links = [u for u in links if u not in already]
        logger.info("root new links source_id=%s count=%s", source_id, len(links))

        for url in links:
            try:
                res = self.fetcher.fetch(url)
                if res.status_code >= 400:
                    continue
                s3_key = self.s3.put_bytes(
                    key_prefix="raw",
                    url=res.url,
                    content=res.body,
                    content_type=res.content_type,
                )
                raw_id = upsert_raw(
                    engine,
                    source_id=source_id,
                    url=res.url,
                    fetched_at=res.fetched_at,
                    status_code=res.status_code,
                    headers=res.headers,
                    content_type=res.content_type,
                    checksum=res.checksum,
                    s3_key=s3_key,
                )
                if raw_id is None:
                    continue
                asyncio.run(
                    bus.publish(
                        f"{self.nats_stream}.raw",
                        {
                            "source_id": source_id,
                            "url": res.url,
                            "fetched_at": res.fetched_at.isoformat(),
                            "status_code": res.status_code,
                            "headers": res.headers,
                            "content_type": res.content_type,
                            "raw_bytes_s3_key": s3_key,
                            "checksum": res.checksum,
                        },
                    )
                )
            except Exception as e:
                logger.warning("fetch article failed url=%s err=%s", url, e)
