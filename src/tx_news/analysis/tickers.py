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

