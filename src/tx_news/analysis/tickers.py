# Input: A 股 name_map（name→ts_code）与待匹配文本
# Output: ticker 实体列表（type/name/ts_code）
# Pos: 朴素 ticker 匹配器（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TickerMatcher:
    # name -> ts_code
    name_map: dict[str, str]

    def match(self, text: str, *, max_hits: int = 20) -> list[dict]:
        hits: list[dict] = []
        for name, ts_code in self.name_map.items():
            if not name or len(name) < 2:
                continue
            if name in text:
                hits.append({"type": "ticker", "name": name, "ts_code": ts_code})
                if len(hits) >= max_hits:
                    break
        return hits
