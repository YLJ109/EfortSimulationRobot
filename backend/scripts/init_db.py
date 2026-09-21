# -*- coding: utf-8 -*-
"""
初始化 SQLite 数据库 (建表)。可单独运行:
    backend/.venv/Scripts/python.exe backend/scripts/init_db.py
"""
import os
import sys

# 允许以脚本方式运行 (把 backend 加入 sys.path)
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core.config import db_path
from app.core.logger import get_logger
from app.db.database import init_db

log = get_logger("init_db")


def main():
    p = db_path()
    log.info("初始化数据库: %s", p)
    init_db()
    log.info("建表完成 (pose_history / events / sessions)")


if __name__ == "__main__":
    main()
