#!/usr/bin/env python3
# Input: 本地运行中的 API base_url + 查询参数
# Output: 知识库检索 API（/search、/articles/{id}）的 PASS/FAIL 与关键响应摘要
# Pos: 演示/回归用脚本：在服务运行状态下验证“知识库 API”端到端可用性（变更时同步更新所属目录 FOLDER.md）

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx


def _http_get_json(client: httpx.Client, url: str, params: dict[str, Any] | None = None) -> Any:
    r = client.get(url, params=params)
    r.raise_for_status()
    return r.json()


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo: test TX-News knowledge base HTTP APIs.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds (default: %(default)s)")
    parser.add_argument("--limit", type=int, default=5, help="search limit (default: %(default)s)")
    parser.add_argument(
        "--require-hit",
        action="store_true",
        help="Fail if /search returns 0 hits (default: allow empty, but print diagnostics).",
    )
    parser.add_argument("--query", default="央行 降准", help="Search query (default: %(default)s)")
    args = parser.parse_args()

    base_url = str(args.base_url).rstrip("/")
    with httpx.Client(timeout=args.timeout) as client:
        # 1) /health
        health = _http_get_json(client, f"{base_url}/health")
        _expect(isinstance(health, dict) and health.get("status") == "ok", f"/health not ok: {health}")

        # 2) /search (knowledge-base retrieval)
        query = str(args.query)
        res = _http_get_json(client, f"{base_url}/search", params={"q": query, "limit": int(args.limit)})
        _expect(isinstance(res, list), f"/search response is not a list: {type(res)}")
        hits = [dict(x) for x in res]
        if args.require_hit:
            _expect(len(hits) > 0, "no hits from /search (require-hit enabled)")

        # 3) follow up with /articles/{canonical_id} for the first hit (should be a real DB record)
        article = None
        if hits:
            cid = str(hits[0].get("canonical_id") or "")
            _expect(bool(cid), "first /search hit has empty canonical_id")
            article = _http_get_json(client, f"{base_url}/articles/{cid}")
            _expect(isinstance(article, dict), f"/articles response is not a dict: {type(article)}")
            _expect(article.get("canonical_id") == cid, "canonical_id mismatch in /articles")

        summary = {
            "health": health,
            "query": query,
            "hits_count": len(hits),
            "hits": hits[: min(len(hits), 3)],
            "article": article,
        }
        sys.stdout.write(json.dumps({"ok": True, "summary": summary}, ensure_ascii=False, indent=2) + "\n")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        sys.stderr.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) + "\n")
        raise
