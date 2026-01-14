# Input: OpenAI 兼容 API key/base_url/模型名/messages/tools + 环境代理（HTTP(S)_PROXY/NO_PROXY）
# Output: LLM chat 结果（json 或 message dict），以及可选的 SSE 流式增量（含超时/握手失败的轻量重试与 IPv4 兜底）
# Pos: 在线 LLM 客户端封装（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
import time
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
    max_retries: int = 4
    force_ipv4: bool = False

    def _timeout(self) -> httpx.Timeout:
        # Keep connect timeout shorter than total so we can retry quickly when DNS returns a "bad" IP that
        # stalls TLS handshake (common in container/NAT environments with partial reachability).
        connect = min(10.0, float(self.timeout_seconds))
        total = float(self.timeout_seconds)
        return httpx.Timeout(connect=connect, read=total, write=total, pool=total)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _is_retryable(self, e: BaseException) -> bool:
        cur: BaseException | None = e
        seen: set[int] = set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if isinstance(cur, (httpx.TimeoutException, httpx.TransportError)):
                return True
            cur = cur.__cause__ or cur.__context__
        return False

    def _client(self, *, force_ipv4: bool) -> httpx.Client:
        transport: httpx.BaseTransport | None = None
        if force_ipv4:
            # Bind to IPv4-only local address to avoid IPv6 "half-open" networks causing TLS handshake timeouts.
            transport = httpx.HTTPTransport(local_address="0.0.0.0")
        return httpx.Client(timeout=self._timeout(), trust_env=True, transport=transport)

    def _post_json_with_retries(self, *, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        attempts = max(0, int(self.max_retries))
        for i in range(attempts + 1):
            try:
                with self._client(force_ipv4=self.force_ipv4) as client:
                    r = client.post(url, headers=headers, json=payload)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if not self.force_ipv4 and self._is_retryable(e):
                    try:
                        with self._client(force_ipv4=True) as client:
                            r = client.post(url, headers=headers, json=payload)
                        r.raise_for_status()
                        return r.json()
                    except Exception as e2:
                        if not self._is_retryable(e2):
                            raise
                        e = e2
                if i >= attempts or not self._is_retryable(e):
                    raise
                time.sleep(0.3 * (2**i))
        raise RuntimeError("unreachable")

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
        try:
            data = self._post_json_with_retries(url=url, headers=headers, payload=payload)
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code in {400, 422}:
                # Some OpenAI-compatible services (or older vLLM) may not support response_format.
                payload.pop("response_format", None)
                data = self._post_json_with_retries(url=url, headers=headers, payload=payload)
            else:
                raise
        content = data["choices"][0]["message"]["content"]
        obj = _extract_json_object(content)
        if not obj:
            raise ValueError("LLM did not return a JSON object")
        return json.loads(obj)

    def _iter_sse_json(self, *, url: str, headers: dict[str, str], payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        with self._client(force_ipv4=self.force_ipv4) as client:
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
        data = self._post_json_with_retries(url=url, headers=headers, payload=payload)
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
        attempts = max(0, int(self.max_retries))
        for i in range(attempts + 1):
            yielded_any = False
            try:
                for item in self._iter_sse_json(url=url, headers=headers, payload=payload):
                    yielded_any = True
                    yield item
                return
            except Exception as e:
                if yielded_any or i >= attempts or not self._is_retryable(e):
                    if (not self.force_ipv4) and self._is_retryable(e) and (not yielded_any):
                        # One-shot IPv4 fallback for "handshake timed out" cases (common in partial IPv6 networks).
                        tmp = DashScopeClient(
                            api_key=self.api_key,
                            model=self.model,
                            base_url=self.base_url,
                            timeout_seconds=self.timeout_seconds,
                            max_retries=0,
                            force_ipv4=True,
                        )
                        yield from tmp._iter_sse_json(url=url, headers=headers, payload=payload)
                        return
                    raise
                time.sleep(0.3 * (2**i))
