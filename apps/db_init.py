# Input: Postgres 连接（TXNEWS_PG_DSN）与 ORM 模型定义
# Output: 确保数据库表已创建（create_all），并输出结果到 stdout
# Pos: 部署引导入口（建表；变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import os
import sys
import time

from tx_news.logging import configure_logging
from tx_news.settings import get_settings
from tx_news.storage.postgres import init_db, make_engine


def _wait_postgres(*, pg_dsn: str, timeout_seconds: int) -> bool:
    engine = make_engine(pg_dsn)
    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() < deadline:
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("select 1")
            return True
        except Exception:
            time.sleep(1)
    return False


def main() -> int:
    configure_logging()
    settings = get_settings()
    pg_timeout = int(os.getenv("TXNEWS_DB_INIT_PG_TIMEOUT_SECONDS", "60"))
    strict = os.getenv("TXNEWS_DB_INIT_STRICT", "0").strip() == "1"

    ok = _wait_postgres(pg_dsn=settings.pg_dsn, timeout_seconds=pg_timeout)
    if not ok:
        msg = f"db-init: postgres not ready (timeout={pg_timeout}s)"
        if strict:
            raise RuntimeError(msg)
        print(msg, file=sys.stderr)
        return 0

    engine = make_engine(settings.pg_dsn)
    init_db(engine)
    print("db-init: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

