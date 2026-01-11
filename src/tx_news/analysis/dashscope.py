# Input: OpenAI 兼容 API key、base_url、模型名、messages/tools 等请求参数
# Output: LLM chat 结果（json 或 message dict），以及可选的 SSE 流式增量
# Pos: 在线 LLM 客户端封装（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from collections.abc import Iterator

import httpx


def _extract_json_object(text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    if s.startswith("{") and s.endswith("}"):
        return s
    i = s.find("{")
    j = s.rfind("}")
    if i < 0 or j < 0 or j <= i:
        return None
    return s[i : j + 1]


@dataclass(frozen=True)
class DashScopeClient:
    api_key: str | None = None
    model: str = "qwen3-max"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_seconds: int = 60

    def _timeout(self) -> httpx.Timeout:
        # Use a shorter connect timeout so we can fail fast and fall back (e.g. to local vLLM).
        connect = min(5.0, float(self.timeout_seconds))
        total = float(self.timeout_seconds)
        return httpx.Timeout(connect=connect, read=total, write=total, pool=total)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def chat_json(self, *, system: str, user: str) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = self._headers()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self._timeout()) as client:
            r = client.post(url, headers=headers, json=payload)
            if r.status_code in {400, 422}:
                # Some OpenAI-compatible services (or older vLLM) may not support response_format.
                payload.pop("response_format", None)
                r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        content = data["choices"][0]["message"]["content"]
        obj = _extract_json_object(content)
        if not obj:
            raise ValueError("LLM did not return a JSON object")
        return json.loads(obj)

    def _iter_sse_json(self, *, url: str, headers: dict[str, str], payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        with httpx.Client(timeout=self._timeout()) as client:
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
        headers = self._headers()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        with httpx.Client(timeout=self._timeout()) as client:
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
        headers = self._headers()
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
