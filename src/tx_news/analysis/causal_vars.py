# Input: causal_variables.yaml whitelist + LLM output affected_variables
# Output: variable whitelist index + normalized affected_variables
# Pos: causal variable whitelist/normalizer (update header and FOLDER.md on change)

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from tx_news.settings import get_settings

DEFAULT_VARIABLES = [
    {
        "name": "Interest Rate",
        "domain": "macro",
        "directions": ["+", "-"],
        "desc": "Policy rate / market rates",
    },
    {
        "name": "Inflation Expectation",
        "domain": "macro",
        "directions": ["+", "-"],
        "desc": "Inflation expectations",
    },
    {"name": "Liquidity", "domain": "macro", "directions": ["+", "-"], "desc": "Market liquidity"},
    {"name": "Risk Premium", "domain": "macro", "directions": ["+", "-"], "desc": "Risk premium"},
    {"name": "Exchange Rate", "domain": "macro", "directions": ["+", "-"], "desc": "FX rate"},
    {"name": "Commodity Price", "domain": "macro", "directions": ["+", "-"], "desc": "Commodity prices"},
    {"name": "Financing Cost", "domain": "firm", "directions": ["+", "-"], "desc": "Corporate funding cost"},
    {"name": "Demand", "domain": "firm", "directions": ["+", "-"], "desc": "Demand"},
    {"name": "Supply", "domain": "firm", "directions": ["+", "-"], "desc": "Supply"},
    {"name": "Input Cost", "domain": "firm", "directions": ["+", "-"], "desc": "Input cost"},
    {"name": "Pricing Power", "domain": "firm", "directions": ["+", "-"], "desc": "Pricing power"},
    {"name": "Profit Margin", "domain": "firm", "directions": ["+", "-"], "desc": "Profit margin"},
    {"name": "Regulatory Cost", "domain": "policy", "directions": ["+", "-"], "desc": "Regulatory burden"},
    {"name": "Tax Burden", "domain": "policy", "directions": ["+", "-"], "desc": "Tax burden"},
    {"name": "Policy Uncertainty", "domain": "policy", "directions": ["+", "-"], "desc": "Policy uncertainty"},
    {"name": "Consumer Confidence", "domain": "household", "directions": ["+", "-"], "desc": "Consumer confidence"},
    {"name": "Disposable Income", "domain": "household", "directions": ["+", "-"], "desc": "Disposable income"},
]


def _safe_float(value: Any, default: float = 0.5) -> float:
    try:
        f = float(value)
    except Exception:
        return default
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _normalize_direction(value: Any) -> str:
    s = str(value or "").strip().lower()
    if s in {"+", "up", "increase", "rising", "bullish", "上涨", "上升", "正"}:
        return "+"
    if s in {"-", "down", "decrease", "falling", "bearish", "下跌", "下降", "负"}:
        return "-"
    if s in {"0", "flat", "neutral", "unchanged", "持平", "中性"}:
        return "0"
    return "uncertain"


def _normalize_alias(s: str) -> str:
    return str(s or "").strip().lower()


@lru_cache(maxsize=1)
def load_causal_variables() -> dict[str, dict[str, Any]]:
    """
    Load causal variable whitelist from config/causal_variables.yaml.
    Returns a canonical-name keyed dict with domain/directions/desc/aliases.
    """
    settings = get_settings()
    path = Path(settings.config_dir) / "causal_variables.yaml"
    data = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    items = data.get("variables") if isinstance(data.get("variables"), list) else None
    if not items:
        items = DEFAULT_VARIABLES

    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        domain = str(item.get("domain") or "").strip() or "macro"
        directions = item.get("directions") if isinstance(item.get("directions"), list) else ["+", "-"]
        applies_to = item.get("applies_to") if isinstance(item.get("applies_to"), dict) else {}
        desc = str(item.get("desc") or "").strip()
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        alias_set = {_normalize_alias(name)} | {_normalize_alias(a) for a in aliases if str(a or "").strip()}
        out[name] = {
            "name": name,
            "domain": domain,
            "directions": [str(d) for d in directions],
            "desc": desc,
            "aliases": sorted(alias_set),
            "applies_to": applies_to,
        }
    return out


def resolve_variable_alias(name: str, var_index: dict[str, dict[str, Any]]) -> str | None:
    key = _normalize_alias(name)
    if not key:
        return None
    for canonical, meta in var_index.items():
        aliases = meta.get("aliases") if isinstance(meta.get("aliases"), list) else []
        if key in {_normalize_alias(canonical)} | {str(a).lower() for a in aliases}:
            return canonical
    return None


def normalize_affected_variables(
    raw: Any,
    *,
    var_index: dict[str, dict[str, Any]],
    max_items: int = 8,
) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name_raw = str(item.get("var") or item.get("name") or item.get("variable") or "").strip()
        canonical = resolve_variable_alias(name_raw, var_index)
        if not canonical:
            continue
        if canonical in seen:
            continue
        direction = _normalize_direction(item.get("direction"))
        conf = _safe_float(item.get("confidence"), default=0.6)
        evidence_raw = item.get("evidence")
        evidence: list[str] = []
        if isinstance(evidence_raw, list):
            for ev in evidence_raw:
                evs = str(ev or "").strip()
                if evs:
                    evidence.append(evs)
                if len(evidence) >= 16:
                    break
        out.append(
            {
                "var": canonical,
                "direction": direction,
                "confidence": conf,
                "evidence": evidence,
            }
        )
        seen.add(canonical)
        if len(out) >= int(max_items):
            break
    return out
