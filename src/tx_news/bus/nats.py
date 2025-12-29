# Input: NATS 连接串、stream/subject 与 payload(dict)
# Output: JetStream stream 初始化与消息发布
# Pos: NATS JetStream 访问封装（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import nats


@dataclass(frozen=True)
class NatsBus:
    url: str
    stream: str

    async def _connect(self):
        return await nats.connect(self.url)

    async def ensure_stream(self) -> None:
        nc = await self._connect()
        try:
            js = nc.jetstream()
            try:
                await js.stream_info(self.stream)
            except Exception:
                await js.add_stream(
                    name=self.stream,
                    subjects=[f"{self.stream}.>"],
                )
        finally:
            await nc.drain()

    async def publish(self, subject: str, payload: dict[str, Any]) -> None:
        nc = await self._connect()
        try:
            js = nc.jetstream()
            await js.publish(subject, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            await nc.drain()
