"""统一日志配置（基于标准库 logging）"""
from __future__ import annotations

import logging
import sys
import os
from logging.handlers import RotatingFileHandler

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 控制台输出
_console = logging.StreamHandler(sys.stderr)
_console.setLevel(LOG_LEVEL)
_console.setFormatter(_formatter)

# 文件输出（生产环境）
os.makedirs("logs", exist_ok=True)
_file = RotatingFileHandler(
    "logs/zhice-agent.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file.setLevel(logging.DEBUG)
_file.setFormatter(_formatter)

logger = logging.getLogger("zhice-agent")
logger.setLevel(LOG_LEVEL)
logger.handlers.clear()
logger.addHandler(_console)
if os.environ.get("ENV") == "production":
    logger.addHandler(_file)

logger.propagate = False

__all__ = ["logger"]
