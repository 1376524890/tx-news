# Input: stdin JSON-RPC 请求 + 本地 Postgres/Qdrant 数据
# Output: stdout JSON-RPC 响应（tools/list, tools/call）
# Pos: MCP 工具服务入口（变更时同步更新以上注释与所属目录 FOLDER.md；并在主数据缺失时尝试从本地缓存引导）

from __future__ import annotations

import json
import sys
from typing import Any

from tx_news.embedding.embedder import DEFAULT_EMBEDDING_MODEL, build_embedder
from tx_news.settings import get_settings
from tx_news.storage.postgres import (
    bootstrap_a_share_basic_from_cache,
    get_a_share,
    get_analysis,
    get_article,
    get_event_canonical_ids,
    get_latest_version,
    init_db,
    make_engine,
)
from tx_news.storage.qdrant import QdrantStore, scored_point_canonical_id


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
            "name": "search_news_full_text",
            "description": "Vector search over KB and include full extracted text (requires TXNEWS_ALLOW_FULL_TEXT=1).",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5}},
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
            "name": "get_article_full_text",
            "description": "Get full extracted text for a canonical article (requires TXNEWS_ALLOW_FULL_TEXT=1).",
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

    allow_full_text = bool(getattr(settings, "allow_full_text", False))

    if name == "search_news":
        q = str(arguments.get("query") or "")
        limit = int(arguments.get("limit") or 10)
        embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
        embedder, qdrant_strategy = build_embedder(embedding_cfg)
        model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
        vector = embedder.embed(q[:2000])
        qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection).resolve_collection_for_embedding(
            vector_size=len(vector),
            model_name_or_path=model_name,
            strategy=qdrant_strategy,
        )
        points = qdrant.search(vector=vector, limit=limit)
        out = []
        for p in points:
            cid = scored_point_canonical_id(p) or str(p.id)
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

    if name == "search_news_full_text":
        if not allow_full_text:
            return {"error": "full_text_disabled"}
        q = str(arguments.get("query") or "")
        limit = int(arguments.get("limit") or 5)
        embedding_cfg = settings.resolve_embedding_cfg(file_cfg)
        embedder, qdrant_strategy = build_embedder(embedding_cfg)
        model_name = str(embedding_cfg.get("model_name") or DEFAULT_EMBEDDING_MODEL)
        vector = embedder.embed(q[:2000])
        qdrant = QdrantStore(url=settings.qdrant_url, collection=settings.qdrant_collection).resolve_collection_for_embedding(
            vector_size=len(vector),
            model_name_or_path=model_name,
            strategy=qdrant_strategy,
        )
        points = qdrant.search(vector=vector, limit=limit)
        out = []
        for p in points:
            cid = scored_point_canonical_id(p) or str(p.id)
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
                    "text": a.text,
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

    if name == "get_article_full_text":
        if not allow_full_text:
            return {"error": "full_text_disabled"}
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
            "text": a.text,
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
            bootstrap = bootstrap_a_share_basic_from_cache(engine)
            row = get_a_share(engine, ts_code)
            if not row:
                return {"error": "not_found", "bootstrap": bootstrap}
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
