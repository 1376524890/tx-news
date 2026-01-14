# Input: OpenAI 兼容 LLM 返回的消息（可流式）+ TxNewsTools 工具查询结果
# Output: 对话回答文本 + 工具调用轨迹 + 证据链接列表（流式失败时自动回退非流式）
# Pos: 工具增强对话 Agent（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from collections.abc import Iterator

import httpx

from tx_news.analysis.dashscope import DashScopeClient
from tx_news.agent.tools import TxNewsTools


class AgentChatError(RuntimeError):
    pass


def _json_loads_maybe(text: str) -> Any | None:
    text = (text or "").strip()
    if not text:
        return None
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


@dataclass
class ToolTrace:
    name: str
    arguments: dict[str, Any]


class TxNewsAgent:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "qwen3-max",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout_seconds: int = 60,
    ) -> None:
        self.client = DashScopeClient(
            api_key=api_key, model=model, base_url=base_url, timeout_seconds=timeout_seconds
        )
        self.tools = TxNewsTools()

        self._tool_funcs: dict[str, Callable[..., Any]] = {
            "search_news": self.tools.search_news,
            "list_recent": self.tools.list_recent,
            "get_article_analysis": self.tools.get_article_analysis,
            "list_signals": self.tools.list_signals,
            "get_event_timeline": self.tools.get_event_timeline,
            "get_entity_profile": self.tools.get_entity_profile,
        }

    def _chat_once_stream(
        self, *, convo: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> Iterator[tuple[dict[str, Any] | None, str | None]]:
        """
        Yield (event, delta_text) while building the assistant message from SSE stream.
        - delta_text: assistant content delta (for UI streaming)
        - event: emits tool call events for observability
        At the end, yields (final_message, None) once.
        """
        content = ""
        role = "assistant"
        tool_calls_by_index: dict[int, dict[str, Any]] = {}

        def _is_network_error(e: BaseException) -> bool:
            cur: BaseException | None = e
            seen: set[int] = set()
            while cur is not None and id(cur) not in seen:
                seen.add(id(cur))
                if isinstance(cur, (httpx.TimeoutException, httpx.TransportError)):
                    return True
                cur = cur.__cause__ or cur.__context__
            return False

        try:
            for chunk in self.client.chat_stream(messages=convo, tools=tools):
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                c0 = choices[0] or {}
                delta = c0.get("delta") or {}
                if "role" in delta:
                    role = delta.get("role") or role
                if "content" in delta and delta.get("content"):
                    d = str(delta.get("content") or "")
                    content += d
                    yield None, d

                # tool_calls are streamed in pieces (arguments are partial strings).
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index") or 0)
                    entry = tool_calls_by_index.setdefault(
                        idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
                    )
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    if tc.get("type"):
                        entry["type"] = tc["type"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        entry["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["function"]["arguments"] += str(fn["arguments"])

                finish_reason = c0.get("finish_reason")
                if finish_reason:
                    break
        except Exception as e:
            # Fallback for flaky provider streaming (TLS handshake timeout etc): use non-stream call and
            # stream a single chunk to the UI, so /chat/stream remains usable.
            if _is_network_error(e) and not content and not tool_calls_by_index:
                msg = self.client.chat(messages=convo, tools=tools)
                role = str(msg.get("role") or role)
                content = str(msg.get("content") or "")
                if content:
                    yield None, content
                out_msg: dict[str, Any] = {"role": role, "content": content}
                if msg.get("tool_calls"):
                    out_msg["tool_calls"] = msg.get("tool_calls")
                yield out_msg, None
                return
            raise

        tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index.keys())]
        for i, call in enumerate(tool_calls, start=1):
            if not call.get("id"):
                call["id"] = f"call_{i}"

        msg: dict[str, Any] = {"role": role, "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        yield msg, None

    def run_stream(
        self,
        *, messages: list[dict[str, str]], max_steps: int = 50, recent_minutes: int = 180
    ) -> Iterator[dict[str, Any]]:
        """
        Stream agent execution as SSE-friendly events.
        Events:
          - {"type":"delta","content":"..."} assistant content delta
          - {"type":"tool_call","name":..., "arguments":...}
          - {"type":"done","message":{role,content,meta}}
        """
        if not messages:
            raise AgentChatError("messages is empty")

        system = (
            "你是 TX-News（高时效经济新闻）助手。你必须遵守：\n"
            "1) 严禁输出新闻原文/大段引用；只允许输出你自己的摘要、结构化结论与可点击 URL。\n"
            "2) 任何结论必须先通过工具检索（list_recent/search_news 等）获取证据。\n"
            "3) 优先处理当天/突发：先调用 list_recent(minutes=%d, limit=20)。\n"
            "4) 输出格式：请尽量使用条列与小标题，给出：结论、影响路径、相关标的、风险与不确定性、证据链接。\n"
            % int(recent_minutes)
        )

        convo: list[dict[str, Any]] = [{"role": "system", "content": system}]
        convo.extend(messages)

        traces: list[ToolTrace] = []
        evidence: list[dict[str, Any]] = []
        evidence_seen: set[str] = set()
        tools = self.tool_specs()

        for step in range(int(max_steps)):
            msg: dict[str, Any] | None = None
            for final_or_none, delta in self._chat_once_stream(convo=convo, tools=tools):
                if delta:
                    yield {"type": "delta", "content": delta}
                if final_or_none is not None:
                    msg = final_or_none

            if not msg:
                raise AgentChatError("empty llm response")

            role = msg.get("role") or "assistant"
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                convo.append({"role": role, "content": content, "tool_calls": tool_calls})
                for call in tool_calls:
                    fn = (call.get("function") or {})
                    name = fn.get("name")
                    args_raw = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    except Exception:
                        args = {}

                    if not name or name not in self._tool_funcs:
                        raise AgentChatError(f"unknown tool: {name}")

                    started_at = time.time()
                    yield {"type": "tool_call", "name": name, "arguments": args}
                    traces.append(ToolTrace(name=name, arguments=args))
                    try:
                        result = self._tool_funcs[name](**args)
                    except Exception as e:
                        elapsed_ms = int((time.time() - started_at) * 1000)
                        yield {
                            "type": "tool_result",
                            "name": name,
                            "ok": False,
                            "duration_ms": elapsed_ms,
                            "summary": {"error": str(e)},
                        }
                        raise
                    elapsed_ms = int((time.time() - started_at) * 1000)
                    yield {
                        "type": "tool_result",
                        "name": name,
                        "ok": True,
                        "duration_ms": elapsed_ms,
                        "summary": self._tool_result_summary(name=name, result=result),
                    }

                    if name in {"search_news", "list_recent"} and isinstance(result, list):
                        for item in result:
                            cid = str(item.get("canonical_id") or "")
                            if not cid or cid in evidence_seen:
                                continue
                            evidence_seen.add(cid)
                            evidence.append(
                                {
                                    "canonical_id": cid,
                                    "source_id": item.get("source_id"),
                                    "url": item.get("url"),
                                    "published_at": item.get("published_at"),
                                }
                            )
                    elif name in {"get_article_analysis"} and isinstance(result, dict):
                        cid = str(result.get("canonical_id") or "")
                        if cid and cid not in evidence_seen:
                            evidence_seen.add(cid)
                            evidence.append(
                                {
                                    "canonical_id": cid,
                                    "source_id": result.get("source_id"),
                                    "url": result.get("latest_url"),
                                    "published_at": result.get("published_at"),
                                }
                            )

                    convo.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(result, ensure_ascii=False)[:20000],
                        }
                    )
                continue

            # Final assistant answer.
            out = {
                "role": "assistant",
                "content": content,
                "meta": {"tools": [t.__dict__ for t in traces], "evidence": evidence},
            }
            yield {"type": "done", "message": out}
            return

        # When max_steps reached, generate final output based on existing information
        yield {
            "type": "delta",
            "content": "\n\n---\n已达到最大工具调用次数，以下是基于现有信息的总结：\n"
        }
        # Ask LLM to summarize based on existing conversation
        convo.append(
            {
                "role": "user",
                "content": "请基于之前的工具调用结果，总结一个最终答案（不允许输出新闻原文，使用 Markdown 格式）。",
            }
        )
        # Generate final summary
        final_msg: dict[str, Any] | None = None
        for final_or_none, delta in self._chat_once_stream(convo=convo, tools=None):
            if delta:
                yield {"type": "delta", "content": delta}
            if final_or_none is not None:
                final_msg = final_or_none
        if final_msg:
            out = {
                "role": "assistant",
                "content": final_msg.get("content") or "",
                "meta": {"tools": [t.__dict__ for t in traces], "evidence": evidence},
            }
            yield {"type": "done", "message": out}
        else:
            # Fallback if no response
            out = {
                "role": "assistant",
                "content": "已达到最大工具调用次数，基于现有信息已完成分析。",
                "meta": {"tools": [t.__dict__ for t in traces], "evidence": evidence},
            }
            yield {"type": "done", "message": out}

    @staticmethod
    def _tool_result_summary(*, name: str, result: Any) -> dict[str, Any]:
        if isinstance(result, list):
            return {"items": len(result)}
        if isinstance(result, dict):
            if result.get("error"):
                return {"error": result.get("error")}
            if name == "get_article_analysis":
                return {"canonical_id": result.get("canonical_id"), "title": result.get("title")}
            if name == "get_entity_profile":
                return {"ts_code": result.get("ts_code"), "name": result.get("name")}
            if name == "get_event_timeline":
                return {"items": len(result.get("items") or [])}
            return {k: result.get(k) for k in ("canonical_id", "event_id", "ts_code") if k in result}
        return {"type": type(result).__name__}

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_recent",
                    "description": "获取最近N分钟内的新闻条目（不返回原文），用于高时效问题的第一步检索。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "minutes": {"type": "integer", "minimum": 5, "maximum": 1440, "default": 180},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 30},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_news",
                    "description": "向量检索知识库（Qdrant）并返回相关新闻元信息与分析摘要（不返回原文）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "q": {"type": "string", "minLength": 1},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                        },
                        "required": ["q"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_article_analysis",
                    "description": "获取单条新闻的分析结果与元信息（不返回原文）。",
                    "parameters": {
                        "type": "object",
                        "properties": {"canonical_id": {"type": "string", "minLength": 1}},
                        "required": ["canonical_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_signals",
                    "description": "获取系统产生的最新信号列表（breaking/analysis_updated 等）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_event_timeline",
                    "description": "按 event_id 获取事件时间线（不返回原文）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "event_id": {"type": "string", "minLength": 1},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                        },
                        "required": ["event_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_entity_profile",
                    "description": "获取 A 股个股基础信息（来自本地数据库，Tushare 同步）。",
                    "parameters": {
                        "type": "object",
                        "properties": {"ts_code": {"type": "string", "minLength": 1}},
                        "required": ["ts_code"],
                    },
                },
            },
        ]

    def run(self, *, messages: list[dict[str, str]], max_steps: int = 50, recent_minutes: int = 180) -> dict[str, Any]:
        if not messages:
            raise AgentChatError("messages is empty")

        system = (
            "你是 TX-News（高时效经济新闻）助手。你必须遵守：\n"
            "1) 严禁输出新闻原文/大段引用；只允许输出你自己的摘要、结构化结论与可点击 URL。\n"
            "2) 任何结论必须先通过工具检索（list_recent/search_news 等）获取证据。\n"
            "3) 优先处理当天/突发：先调用 list_recent(minutes=%d, limit=20)。\n"
            "4) 输出格式：请尽量使用条列与小标题，给出：结论、影响路径、相关标的、风险与不确定性、证据链接。\n"
            % int(recent_minutes)
        )

        convo: list[dict[str, Any]] = [{"role": "system", "content": system}]
        convo.extend(messages)

        traces: list[ToolTrace] = []
        evidence: list[dict[str, Any]] = []
        evidence_seen: set[str] = set()

        tools = self.tool_specs()

        for step in range(int(max_steps)):
            msg = self.client.chat(messages=convo, tools=tools)
            role = msg.get("role") or "assistant"
            content = msg.get("content") or ""

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls and msg.get("function_call"):
                tool_calls = [{"id": "call_1", "type": "function", "function": msg["function_call"]}]
            if tool_calls:
                convo.append({"role": role, "content": content, "tool_calls": tool_calls})
                for call in tool_calls:
                    fn = (call.get("function") or {})
                    name = fn.get("name")
                    args_raw = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    except Exception:
                        args = {}

                    if not name or name not in self._tool_funcs:
                        raise AgentChatError(f"unknown tool: {name}")

                    traces.append(ToolTrace(name=name, arguments=args))
                    result = self._tool_funcs[name](**args)

                    # Collect evidence opportunistically.
                    if name in {"search_news", "list_recent"} and isinstance(result, list):
                        for item in result:
                            cid = str(item.get("canonical_id") or "")
                            if not cid or cid in evidence_seen:
                                continue
                            evidence_seen.add(cid)
                            evidence.append(
                                {
                                    "canonical_id": cid,
                                    "source_id": item.get("source_id"),
                                    "url": item.get("url"),
                                    "published_at": item.get("published_at"),
                                }
                            )
                    elif name in {"get_article_analysis"} and isinstance(result, dict):
                        cid = str(result.get("canonical_id") or "")
                        if cid and cid not in evidence_seen:
                            evidence_seen.add(cid)
                            evidence.append(
                                {
                                    "canonical_id": cid,
                                    "source_id": result.get("source_id"),
                                    "url": result.get("latest_url"),
                                    "published_at": result.get("published_at"),
                                }
                            )

                    convo.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(result, ensure_ascii=False)[:20000],
                        }
                    )
                continue

            # If the model answered without using tools, enforce the workflow once.
            if not traces:
                convo.append({"role": role, "content": content})
                convo.append(
                    {
                        "role": "user",
                        "content": f"请先调用工具 `list_recent(minutes={int(recent_minutes)}, limit=20)` 获取证据，再基于证据回答（不允许输出新闻原文）。",
                    }
                )
                traces.append(ToolTrace(name="(enforce_tools)", arguments={"recent_minutes": int(recent_minutes)}))
                continue

            parsed = _json_loads_maybe(content)
            if isinstance(parsed, dict) and "answer" in parsed:
                answer = str(parsed.get("answer") or "")
                ev = parsed.get("evidence")
                if isinstance(ev, list):
                    evidence = ev  # trust model if provided
                return {
                    "role": "assistant",
                    "content": answer,
                    "meta": {"tools": [t.__dict__ for t in traces], "evidence": evidence},
                }

            # Plain text fallback.
            return {
                "role": "assistant",
                "content": content or "（空响应）",
                "meta": {"tools": [t.__dict__ for t in traces], "evidence": evidence},
            }

        # When max_steps reached, generate final output based on existing information
        # Ask LLM to summarize based on existing conversation
        convo.append(
            {
                "role": "user",
                "content": "请基于之前的工具调用结果，总结一个最终答案（不允许输出新闻原文，使用 Markdown 格式）。",
            }
        )
        # Generate final summary
        final_msg = self.client.chat(messages=convo, tools=None)
        final_content = final_msg.get("content") or "已达到最大工具调用次数，基于现有信息已完成分析。"
        return {
            "role": "assistant",
            "content": final_content,
            "meta": {"tools": [t.__dict__ for t in traces], "evidence": evidence},
        }
