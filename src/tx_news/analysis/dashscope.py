# Input: OpenAI 兼容 API key、base_url、模型名、messages/tools 等请求参数
# Output: LLM chat 结果（json 或 message dict），以及可选的 SSE 流式增量
# Pos: 在线 LLM 客户端封装（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from collections.abc import Iterator

import httpx


@dataclass(frozen=True)
class DashScopeClient:
    api_key: str
    model: str = "qwen3-max"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_seconds: int = 60

    def chat_json(self, *, system: str, user: str) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)

    def _iter_sse_json(self, *, url: str, headers: dict[str, str], payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            with client.stream("POST", url, headers=headers, json=payload) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    s = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else str(line)
                    s = s.strip()
                    if not s.startswith("data:"):
                        continue
                    data = s[len("data:") :].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        return
                    yield json.loads(data)

    def chat(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"]

    def chat_stream(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> Iterator[dict[str, Any]]:
        """
        Stream OpenAI-compatible chat.completions SSE events.
        Yields parsed JSON payloads for each `data: {...}` line (excluding [DONE]).
        """
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        yield from self._iter_sse_json(url=url, headers=headers, payload=payload)
