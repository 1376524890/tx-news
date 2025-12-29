# Input: 环境变量 TXNEWS_LOG_LEVEL
# Output: logging.basicConfig 配置后的全局日志行为
# Pos: 日志配置入口（变更时同步更新以上注释与所属目录 FOLDER.md）

from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level = os.environ.get("TXNEWS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
