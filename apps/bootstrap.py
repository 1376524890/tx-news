# Input: Postgres 连接 + config/config.yaml（可选）+ 网络/缓存（可选）
# Output: 首次启动时引导主数据（a_share_basic），并输出结果到 stdout
# Pos: 部署引导入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import os
import sys
import time

from tx_news.logging import configure_logging
from tx_news.settings import get_settings
from tx_news.storage.postgres import a_share_basic_has_any, init_db, make_engine
from tx_news.tasks.maintenance import sync_tushare


def _wait_postgres(*, pg_dsn: str, timeout_seconds: int) -> bool:
    engine = make_engine(pg_dsn)
    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() < deadline:
        try:
            init_db(engine)
            with engine.connect() as conn:
                conn.exec_driver_sql("select 1")
            return True
        except Exception:
            time.sleep(1)
    return False


def main() -> int:
    configure_logging()
    settings = get_settings()
    pg_timeout = int(os.getenv("TXNEWS_BOOTSTRAP_PG_TIMEOUT_SECONDS", "60"))
    strict = os.getenv("TXNEWS_BOOTSTRAP_STRICT", "0").strip() == "1"

    ok = _wait_postgres(pg_dsn=settings.pg_dsn, timeout_seconds=pg_timeout)
    if not ok:
        msg = f"bootstrap: postgres not ready (timeout={pg_timeout}s)"
        if strict:
            raise RuntimeError(msg)
        print(msg, file=sys.stderr)
        return 0

    engine = make_engine(settings.pg_dsn)
    init_db(engine)

    if a_share_basic_has_any(engine):
        print("bootstrap: a_share_basic already populated; skip")
        return 0

    try:
        res = sync_tushare.apply().get()
        print({"bootstrap": "sync_tushare", "result": res})
        return 0
    except Exception as e:
        if strict:
            raise
        print({"bootstrap": "sync_tushare", "error": str(e)}, file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

