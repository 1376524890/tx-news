# Input: 新闻标题/正文与事件窗口配置
# Output: event_type、stable_event_id、窗口起点等规则计算结果
# Pos: 规则分析组件（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


EVENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("policy", ["国务院", "央行", "证监会", "监管", "政策", "条例", "意见", "指导", "通知"]),
    ("macro_data", ["CPI", "PPI", "PMI", "GDP", "社融", "M2", "失业率", "通胀", "利率", "降准", "加息", "降息"]),
    ("company_event", ["财报", "业绩", "预告", "回购", "增持", "减持", "重组", "并购", "破产", "退市", "诉讼"]),
    ("geopolitics", ["制裁", "冲突", "战争", "关税", "出口管制", "地缘", "谈判", "停火"]),
    ("industry_supply_demand", ["产能", "供给", "需求", "库存", "价格", "涨价", "限产", "复产", "停产"]),
    ("liquidity", ["流动性", "融资", "融券", "杠杆", "资金面", "回购利率", "隔夜"]),
]


def classify_event_type(text: str, title: str | None = None) -> str:
    hay = f"{title or ''}\n{text}"
    for et, kws in EVENT_KEYWORDS:
        if any(k in hay for k in kws):
            return et
    return "other"


def stable_event_id(event_type: str, key: str, window_start_iso: str) -> str:
    s = f"{event_type}|{key}|{window_start_iso}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def pick_key_entity(entities: list[dict[str, Any]]) -> str:
    # Prefer A-share tickers or organization names; fallback to empty
    for e in entities:
        if e.get("type") == "ticker" and e.get("ts_code"):
            return str(e["ts_code"])
    for e in entities:
        if e.get("type") in ("org", "company") and e.get("name"):
            return str(e["name"])
    return ""


@dataclass(frozen=True)
class EventWindowPlanner:
    event_windows_minutes: dict[str, int]

    def window_minutes(self, event_type: str) -> int:
        return int(self.event_windows_minutes.get(event_type, 1440))

    def window_start(self, *, event_type: str, dt: datetime) -> datetime:
        minutes = self.window_minutes(event_type)
        # align to minutes since epoch
        epoch_min = int(dt.timestamp() // 60)
        start_min = (epoch_min // minutes) * minutes
        return datetime.fromtimestamp(start_min * 60, tz=dt.tzinfo or timezone.utc)
