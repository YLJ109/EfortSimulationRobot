# -*- coding: utf-8 -*-
"""引擎/会话/建库。CPU 绑定 SQLite, 关闭同进程内多线程检查。"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import db_path
from app.db.models import Base

_engine = None
_SessionLocal = None


def _engine_url() -> str:
    """数据库连接串：环境变量 EFORT_DB_URL 优先（测试/部署可覆盖），
    否则用默认 SQLite 文件绝对路径。"""
    override = os.environ.get("EFORT_DB_URL")
    if override:
        return override
    return f"sqlite:///{db_path()}"


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(
            _engine_url(),
            connect_args={"check_same_thread": False},
            future=True,
        )

        # P1-3: 启用 WAL + busy_timeout, 避免采集写入与 API 读取并发时 "database is locked"
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=5000")
            finally:
                cur.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, future=True)
    return _engine


def SessionLocal():
    if _SessionLocal is None:
        raise RuntimeError("数据库未初始化, 请先调用 init_db()")
    return _SessionLocal()


def _migrate(eng) -> None:
    """轻量迁移: create_all 不会给已存在的表加列, 这里按需补列(幂等)。"""
    insp = inspect(eng)
    if "recordings" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("recordings")}
    if "source" not in cols:
        with eng.begin() as conn:
            conn.execute(text("ALTER TABLE recordings ADD COLUMN source VARCHAR(16) DEFAULT 'sim'"))


def init_db() -> None:
    eng = get_engine()
    Base.metadata.create_all(eng)
    _migrate(eng)
