# Input: NATS JetStream raw 消息流 + Redis(Celery broker)
# Output: 调用 tx_news.tasks.pipeline.ingest_raw 入队 Celery
# Pos: NATS→Celery 桥接进程入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import asyncio
import json
import logging

import nats

from tx_news.logging import configure_logging
from tx_news.settings import get_settings
from tx_news.tasks.pipeline import ingest_raw

logger = logging.getLogger(__name__)


async def main_async() -> None:
    settings = get_settings()
    nc = await nats.connect(settings.nats_url)
    js = nc.jetstream()

    # Ensure stream exists
    try:
        await js.stream_info(settings.nats_stream)
    except Exception:
        await js.add_stream(name=settings.nats_stream, subjects=[f"{settings.nats_stream}.>"])

    sub = await js.subscribe(
        subject=f"{settings.nats_stream}.raw",
        durable="txnews_raw_bridge",
        stream=settings.nats_stream,
        manual_ack=True,
    )

    logger.info("listening nats subject=%s", f"{settings.nats_stream}.raw")
    async for msg in sub.messages:
        try:
            payload = json.loads(msg.data.decode("utf-8"))
            ingest_raw.delay(payload)
            await msg.ack()
        except Exception as e:
            logger.exception("failed to handle message: %s", e)
            # do not ack -> redelivery


def main() -> None:
    configure_logging()
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
