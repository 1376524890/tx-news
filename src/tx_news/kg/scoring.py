# Input: two events (type + tickers overlap)
# Output: related_to weight + reason_text (<= 800 chars)
# Pos: v2 KG edge scoring rules（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def related_to_reason_and_weight(
    *,
    event_type_a: str,
    tickers_a: list[str],
    event_type_b: str,
    tickers_b: list[str],
    max_reason_chars: int = 800,
) -> tuple[float, str]:
    """
    Deterministic scoring:
    - base = Jaccard overlap of tickers
    - +0.2 if event_type matches
    - clipped to [0, 1]
    """
    a = {str(x or "").strip().upper() for x in (tickers_a or []) if str(x or "").strip()}
    b = {str(x or "").strip().upper() for x in (tickers_b or []) if str(x or "").strip()}

    inter = sorted(a & b)
    union = a | b
    base = (len(inter) / len(union)) if union else 0.0

    bonus = 0.2 if (str(event_type_a or "").strip() and str(event_type_a).strip() == str(event_type_b).strip()) else 0.0
    w = max(0.0, min(1.0, base + bonus))

    reason = []
    if inter:
        reason.append(f"shared_tickers={','.join(inter[:8])}")
    else:
        reason.append("shared_tickers=-")
    if bonus > 0:
        reason.append("event_type_bonus=0.2")
    reason.append(f"weight={w:.3f}")

    return w, _clip("; ".join(reason), int(max_reason_chars))

