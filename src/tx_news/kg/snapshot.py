# Input: event_type + tickers + evidence articles
# Output: rule-based snapshot_text (<= 2000 chars)
# Pos: v2 KG snapshot builder（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from datetime import datetime
from typing import Any


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def build_event_snapshot_text(
    *,
    event_id: str,
    event_type: str,
    tickers: list[str],
    articles: list[dict[str, Any]],
    impact: dict[str, Any] | None,
    max_chars: int = 2000,
) -> str:
    """
    Fixed template snapshot_text for stability (no LLM required).
    `articles` expects newest-first and contains: title,url,published_at.
    """
    et = (event_type or "other").strip() or "other"
    tks = [str(x or "").strip().upper() for x in (tickers or [])]
    tks = [x for x in tks if x]
    tks = tks[:6]

    direction = None
    conf = None
    if isinstance(impact, dict):
        direction = impact.get("direction")
        conf = impact.get("confidence")
    direction_s = str(direction or "uncertain")
    conf_s = f"{int(float(conf) * 100)}%" if isinstance(conf, (int, float)) else "-"

    lines: list[str] = []
    lines.append(f"Event: {et}")
    lines.append(f"EventID: {event_id}")
    lines.append(f"Key tickers: {', '.join(tks) if tks else '-'}")
    lines.append(f"Impact: {direction_s} (conf={conf_s})")
    lines.append("")
    lines.append("Latest evidence:")
    for i, a in enumerate((articles or [])[:3], start=1):
        title = _clip(str(a.get('title') or ''), 120) or "(untitled)"
        url = str(a.get("url") or "")
        ts = str(a.get("published_at") or a.get("created_at") or "")
        # keep timestamp compact if possible
        try:
            if ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = dt.isoformat()
        except Exception:
            pass
        lines.append(f"{i}. {title}")
        if ts:
            lines.append(f"   time: {ts}")
        if url:
            lines.append(f"   url: {url}")

    text = "\n".join(lines).strip()
    return _clip(text, int(max_chars))

