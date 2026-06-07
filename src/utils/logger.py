"""统一日志配置"""

import sys
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> — <level>{message}</level>",
    level="INFO",
)
logger.add(
    "logs/zhice-agent.log",
    rotation="10 MB",
    retention="30 days",
    compression="zip",
    level="DEBUG",
)

__all__ = ["logger"]
