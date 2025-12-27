from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import tushare as ts


@dataclass(frozen=True)
class TushareSync:
    token: str

    def fetch_stock_basic(self) -> list[dict[str, Any]]:
        ts.set_token(self.token)
        pro = ts.pro_api()
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,area,industry,market,list_date")
        rows = df.to_dict(orient="records")
        # add aliases placeholder
        for r in rows:
            r["aliases"] = {"names": [r.get("name")]}
        return rows

