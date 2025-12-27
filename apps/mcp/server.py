from __future__ import annotations

import json
import sys
from typing import Any

from tx_news.embedding.embedder import Embedder
from tx_news.settings import get_settings
from tx_news.storage.postgres import (
    get_a_share,
    get_analysis,
    get_article,
    get_event_canonical_ids,
    get_latest_version,
    init_db,
    make_engine,
)
from tx_news.storage.qdrant import QdrantStore


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def tools_list() -> list[dict[str, Any]]:
    return [
        {
            "name": "search_news",
            "description": "Vector-first search over the news knowledge base.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
                "required": ["query"],
            },
        },
        {
            "name": "get_article_analysis",
            "description": "Get latest structured analysis for a canonical article (no full text).",
            "inputSchema": {
                "type": "object",
                "properties": {"canonical_id": {"type": "string"}},
                "required": ["canonical_id"],
            },
        },
        {
            "name": "get_event_timeline",
            "description": "Get a simple timeline (list of related articles) for an event_id.",
            "inputSchema": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}, "limit": {"type": "integer", "default": 50}},
                "required": ["event_id"],
            },
        },
        {
            "name": "get_entity_profile",
            "description": "Get A-share basic profile by ts_code (e.g. 000001.SZ).",
            "inputSchema": {
                "type": "object",
                "properties": {"ts_code": {"type": "string"}},
                "required": ["ts_code"],
            },
        },
    ]


def tool_call(name: str, arguments: dict[str, Any]) -> Any:
    settings = get_settings()
    file_cfg = settings.load_file_settings()
    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    if name == "search_news":
        q = str(arguments.get("query") or "")
        limit = int(arguments.get("limit") or 10)
        model_name = (file_cfg.embedding or {}).get("model_name", "bge-small-zh-v1.5")
        vector = Embedder(model_name_or_path=model_name).embed(q[:2000])
        qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection)
        points = qdrant.search(vector=vector, limit=limit)
        out = []
        for p in points:
            cid = str(p.id)
            a = get_article(engine, cid)
            v = get_latest_version(engine, cid)
            an = get_analysis(engine, cid)
            if not a:
                continue
            out.append(
                {
                    "canonical_id": cid,
                    "score": float(p.score or 0.0),
                    "title": a.title,
                    "url": v.url if v else None,
                    "published_at": v.published_at.isoformat() if v and v.published_at else None,
                    "analysis": an.data if an else None,
                }
            )
        return out

    if name == "get_article_analysis":
        cid = str(arguments.get("canonical_id") or "")
        a = get_article(engine, cid)
        if not a:
            return {"error": "not_found"}
        v = get_latest_version(engine, cid)
        an = get_analysis(engine, cid)
        return {
            "canonical_id": cid,
            "title": a.title,
            "latest_url": v.url if v else None,
            "published_at": v.published_at.isoformat() if v and v.published_at else None,
            "analysis": an.data if an else None,
        }

    if name == "get_event_timeline":
        event_id = str(arguments.get("event_id") or "")
        limit = int(arguments.get("limit") or 50)
        canonical_ids = get_event_canonical_ids(engine, event_id, limit=limit)
        out = []
        for cid in canonical_ids:
            a = get_article(engine, cid)
            if not a:
                continue
            v = get_latest_version(engine, cid)
            an = get_analysis(engine, cid)
            out.append(
                {
                    "canonical_id": cid,
                    "title": a.title,
                    "url": v.url if v else None,
                    "published_at": v.published_at.isoformat() if v and v.published_at else None,
                    "analysis": an.data if an else None,
                }
            )
        return out

    if name == "get_entity_profile":
        ts_code = str(arguments.get("ts_code") or "")
        row = get_a_share(engine, ts_code)
        if not row:
            return {"error": "not_found"}
        return {
            "ts_code": row.ts_code,
            "name": row.name,
            "industry": row.industry,
            "area": row.area,
            "market": row.market,
            "list_date": row.list_date,
            "aliases": row.aliases,
            "updated_at": row.updated_at.isoformat(),
        }

    raise KeyError(f"unknown tool: {name}")


def main() -> None:
    # Minimal MCP-like JSON-RPC over stdio (sufficient for tool calls).
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            _write(_error(None, -32700, "parse error"))
            continue

        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        try:
            if method in ("initialize", "mcp/initialize"):
                _write({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "0.1.0", "serverInfo": {"name": "tx-news-mcp", "version": "0.1.0"}}})
                continue
            if method in ("tools/list", "mcp/tools/list"):
                _write({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools_list()}})
                continue
            if method in ("tools/call", "mcp/tools/call"):
                name = params.get("name")
                arguments = params.get("arguments") or {}
                result = tool_call(str(name), dict(arguments))
                _write({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "json", "json": result}]}})
                continue
            _write(_error(rid, -32601, f"method not found: {method}"))
        except Exception as e:
            _write(_error(rid, -32000, str(e)))


if __name__ == "__main__":
    main()
