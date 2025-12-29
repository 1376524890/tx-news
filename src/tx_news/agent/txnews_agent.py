from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

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
    def __init__(self, *, api_key: str, model: str = "qwen3-max") -> None:
        self.client = DashScopeClient(api_key=api_key, model=model)
        self.tools = TxNewsTools()

        self._tool_funcs: dict[str, Callable[..., Any]] = {
            "search_news": self.tools.search_news,
            "list_recent": self.tools.list_recent,
            "get_article_analysis": self.tools.get_article_analysis,
            "list_signals": self.tools.list_signals,
            "get_event_timeline": self.tools.get_event_timeline,
            "get_entity_profile": self.tools.get_entity_profile,
        }

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

    def run(self, *, messages: list[dict[str, str]], max_steps: int = 6, recent_minutes: int = 180) -> dict[str, Any]:
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

        for _ in range(int(max_steps)):
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

        raise AgentChatError("max_steps exceeded (tool loop did not terminate)")
