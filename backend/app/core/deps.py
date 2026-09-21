# -*- coding: utf-8 -*-
"""FastAPI 依赖注入。"""
from __future__ import annotations

from typing import Generator

from app.db.database import SessionLocal


def get_db() -> Generator:
    """每个请求一个数据库会话，结束时确保关闭（含异常路径）。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
