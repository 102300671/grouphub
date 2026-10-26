"""日志：共享包只用标准库 logging。

共享包不得依赖 nonebot 的 loguru（backend 端没有 nonebot）。
QQ 端在 plugins/_lib/aitools_host.py 里把 shared.aitools 的日志桥接到 loguru，
保证终端与文件日志的格式、级别跟其它模块一致。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("shared.aitools")
logger.addHandler(logging.NullHandler())


def log_exception(message: str, exc: BaseException) -> None:
    """记录异常堆栈（loguru 与标准库 API 不同，统一封装）。"""
    logger.error(message, exc_info=exc)
