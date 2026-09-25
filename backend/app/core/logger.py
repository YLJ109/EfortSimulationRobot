# -*- coding: utf-8 -*-
"""统一日志: 同时输出到控制台与 logs/app.log。

★ 审计修复 P0-6：原来是 `logging.FileHandler` **无轮转**。
  后端 7×24 运行、每条请求/采集/下发都写日志，实测增长 15~30 MB/天，
  一年就是 5~10 GB，最终把运行盘写满 → 采集失败、服务起不来。
  现改为 RotatingFileHandler（默认 10 MB × 10 个备份 ≈ 110 MB 封顶）。

★ 审计修复 P2-C：级别不再硬编码，可用 EFORT_LOG_LEVEL=DEBUG/INFO/WARNING 调整。
  时间戳时区见 EFORT_LOG_TZ 说明（DB 存 UTC、前端 new Date() 展示本地时间，
  所以日志默认保持**本地时间**与界面一致；要和 DB 原值对账时设 EFORT_LOG_TZ=utc）。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from app.core.config import logs_dir

_LOG_FILE = "app.log"

# ★ P0-6：轮转参数可用环境变量调（单位字节 / 个数）
_MAX_BYTES = int(os.environ.get("EFORT_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
_BACKUP_COUNT = int(os.environ.get("EFORT_LOG_BACKUP", "10"))

# ★ 关键：多个 logger（main/modbus/collector/...）**共用同一个 FileHandler 实例**。
#   否则每个 logger 各开一个 handler 各自轮转，会互相把对方正在写的文件 rename 掉，
#   造成日志丢失/交叉覆盖。
_file_handlers: dict[str, RotatingFileHandler] = {}
_fh_lock = threading.Lock()


def _utc_log() -> bool:
    return os.environ.get("EFORT_LOG_TZ", "local").strip().lower() in ("utc", "z", "gmt")


def _make_formatter() -> logging.Formatter:
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if _utc_log():
        fmt.converter = time.gmtime   # 与 DB/API 的 UTC 原值对齐
    return fmt


def _file_handler(path: str, fmt: logging.Formatter) -> RotatingFileHandler:
    with _fh_lock:
        h = _file_handlers.get(path)
        if h is None:
            h = RotatingFileHandler(
                path,
                maxBytes=_MAX_BYTES,        # 单文件上限
                backupCount=_BACKUP_COUNT,  # 保留 app.log.1 ~ .10
                encoding="utf-8",
            )
            h.setFormatter(fmt)
            _file_handlers[path] = h
        return h


def get_logger(name: str = "efort") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    try:
        level = os.environ.get("EFORT_LOG_LEVEL", "INFO").strip().upper()
    except Exception:
        level = "INFO"
    logger.setLevel(getattr(logging, level, logging.INFO))
    fmt = _make_formatter()

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    try:
        logger.addHandler(_file_handler(os.path.join(logs_dir(), _LOG_FILE), fmt))
    except Exception:
        # 日志文件不可用不应阻断主程序
        pass

    logger.propagate = False
    return logger
