#!/usr/bin/env python3
# Input: 本地运行中的 API base_url + 查询参数
# Output: 数据库相关 HTTP API（/status、/signals、/articles/{id} 等）可用性检查的 PASS/FAIL 与关键指标
# Pos: 演示/回归用脚本：在服务运行状态下验证“数据库 API”可用（变更时同步更新所属目录 FOLDER.md）

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _http_get_json(client: httpx.Client, url: str) -> Any:
    r = client.get(url)
    r.raise_for_status()
    return r.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo: test TX-News DB-backed HTTP APIs.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds (default: %(default)s)")
    parser.add_argument(
        "--require-non-empty",
        action="store_true",
        help="Fail if DB-backed endpoints return 0 real rows (default: allow empty, but print diagnostics).",
    )
    parser.add_argument("--signals-limit", type=int, default=5, help="signals limit (default: %(default)s)")
    args = parser.parse_args()

    base = str(args.base_url).rstrip("/")
    with httpx.Client(timeout=args.timeout) as client:
        health = _http_get_json(client, f"{base}/health")
        _expect(isinstance(health, dict) and health.get("status") == "ok", f"/health not ok: {health}")

        # 1) /status should report postgres ok and expose counts (DB connectivity + basic queries).
        status = _http_get_json(client, f"{base}/status")
        _expect(isinstance(status, dict), f"/status response is not a dict: {type(status)}")
        deps = status.get("dependencies")
        _expect(isinstance(deps, dict), "/status missing dependencies dict")
        pg = deps.get("postgres")
        _expect(isinstance(pg, dict), "/status dependencies.postgres missing dict")
        _expect(bool(pg.get("ok")), f"/status dependencies.postgres.ok is not true: {pg}")

        counts = status.get("counts")
        if args.require_non_empty:
            _expect(isinstance(counts, dict), "/status missing counts dict (require-non-empty enabled)")
            _expect(
                int(counts.get("signals") or 0) > 0 or int(counts.get("articles") or 0) > 0,
                "no real DB rows (signals/articles are both 0)",
            )

        # 2) /signals returns real rows from Postgres.
        signals = _http_get_json(client, f"{base}/signals?limit={int(args.signals_limit)}")
        _expect(isinstance(signals, list), f"/signals response is not a list: {type(signals)}")
        if args.require_non_empty:
            _expect(len(signals) > 0, "no rows from /signals (require-non-empty enabled)")

        # 3) If we got a canonical_id from signals, validate /articles/{id} (DB read path).
        article = None
        if signals:
            cid = str((signals[0] or {}).get("canonical_id") or "")
            if cid:
                article = _http_get_json(client, f"{base}/articles/{cid}")
                _expect(isinstance(article, dict), f"/articles response is not a dict: {type(article)}")
                _expect(article.get("canonical_id") == cid, "canonical_id mismatch in /articles")

    out = {
        "ok": True,
        "base_url": base,
        "health": health,
        "status": {"dependencies": deps, "counts": counts},
        "signals_count": len(signals),
        "signals_sample": signals[: min(len(signals), 3)],
        "article_sample": article,
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        sys.stderr.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) + "\n")
        raise
