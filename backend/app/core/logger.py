# -*- coding: utf-8 -*-
"""统一日志: 同时输出到控制台与 logs/app.log。"""
from __future__ import annotations

import logging
import os
import sys

from app.core.config import logs_dir

_LOG_FILE = "app.log"


def get_logger(name: str = "efort") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    try:
        fh = logging.FileHandler(
            os.path.join(logs_dir(), _LOG_FILE), encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        # 日志文件不可用不应阻断主程序
        pass

    logger.propagate = False
    return logger
