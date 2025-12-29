# Input: config/config.yaml 的 tushare.token（可选）与网络/缓存
# Output: 同步 A 股主数据到 Postgres（通过 maintenance 任务实现）
# Pos: 手动同步入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

from tx_news.logging import configure_logging
from tx_news.tasks.maintenance import sync_tushare


def main() -> None:
    configure_logging()
    # run synchronously via Celery task function
    res = sync_tushare.apply().get()
    print(res)


if __name__ == "__main__":
    main()
