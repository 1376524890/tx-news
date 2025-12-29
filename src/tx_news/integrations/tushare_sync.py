# Input: Tushare token/网络 + 本地缓存文件
# Output: A 股主数据 rows 列表（并写入 var/cache/a_share/stock_basic.json）
# Pos: Tushare/AkShare 集成与缓存（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tushare as ts


@dataclass(frozen=True)
class TushareSync:
    token: str

    def _cache_path(self) -> Path:
        # New canonical path (kept stable across data providers).
        return Path("var/cache/a_share/stock_basic.json")

    def _legacy_cache_path(self) -> Path:
        return Path("var/cache/tushare/stock_basic.json")

    def _read_cache_payload(self) -> dict[str, Any] | None:
        path = self._cache_path()
        if not path.exists():
            path = self._legacy_cache_path()
            if not path.exists():
                return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def load_cached_stock_basic(self) -> list[dict[str, Any]] | None:
        payload = self._read_cache_payload()
        if not payload:
            return None
        rows = payload.get("rows")
        if isinstance(rows, list):
            return rows
        return None

    def cache_is_fresh(self, *, ttl_seconds: int) -> bool:
        payload = self._read_cache_payload()
        if not payload:
            return False
        fetched_at = payload.get("fetched_at")
        if not fetched_at or not isinstance(fetched_at, str):
            return False
        try:
            ts_dt = datetime.fromisoformat(fetched_at)
        except Exception:
            return False
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts_dt.astimezone(timezone.utc)
        return age.total_seconds() <= int(ttl_seconds)

    def save_stock_basic_cache(self, rows: list[dict[str, Any]], *, source: str) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "source": source, "fetched_at": datetime.now(timezone.utc).isoformat(), "rows": rows}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def fetch_stock_basic_tushare(self) -> list[dict[str, Any]]:
        ts.set_token(self.token)
        pro = ts.pro_api()
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,area,industry,market,list_date")
        rows = df.to_dict(orient="records")
        # add aliases placeholder
        for r in rows:
            r["aliases"] = {"names": [r.get("name")]}
        self.save_stock_basic_cache(rows, source="tushare")
        return rows

    def _infer_suffix(self, code: str) -> str | None:
        c = (code or "").strip()
        if len(c) != 6 or not c.isdigit():
            return None
        if c.startswith(("60", "68", "90")) or c.startswith("6"):
            return "SH"
        if c.startswith(("00", "30", "20")) or c.startswith(("0", "3")):
            return "SZ"
        if c.startswith(("4", "8")):
            return "BJ"
        return None

    def _to_ts_code(self, code: str) -> str | None:
        suffix = self._infer_suffix(code)
        if not suffix:
            return None
        return f"{code}.{suffix}"

    def fetch_stock_basic_akshare(self) -> list[dict[str, Any]]:
        try:
            import akshare as ak  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(f"akshare import failed: {e}") from e

        df = ak.stock_info_a_code_name()
        rows: list[dict[str, Any]] = []
        for r in df.to_dict(orient="records"):
            code = str(r.get("code") or "").strip()
            name = str(r.get("name") or "").strip()
            ts_code = self._to_ts_code(code)
            if not ts_code or not name:
                continue
            rows.append(
                {
                    "ts_code": ts_code,
                    "name": name,
                    "area": None,
                    "industry": None,
                    "market": None,
                    "list_date": None,
                    "aliases": {"names": [name]},
                }
            )

        if not rows:
            raise RuntimeError("akshare returned empty stock_basic")
        self.save_stock_basic_cache(rows, source="akshare")
        return rows

    def fetch_stock_basic(self) -> list[dict[str, Any]]:
        """
        Preferred: Tushare (richer fields).
        Fallback: AkShare (code+name baseline).
        """
        if self.token.strip():
            return self.fetch_stock_basic_tushare()
        return self.fetch_stock_basic_akshare()
