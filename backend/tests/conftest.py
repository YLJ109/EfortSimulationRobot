# -*- coding: utf-8 -*-
"""pytest 共享 fixture。

测试环境策略：
- EFORT_SIMULATE=always  强制模拟，避免测试时连接真实 Modbus (192.168.1.12:502)
- EFORT_DB_URL 指向临时 SQLite，不污染生产 data/robot.db
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    db_file = tmp_path_factory.mktemp("testdb") / "test.db"
    os.environ["EFORT_DB_URL"] = f"sqlite:///{db_file}"
    os.environ["EFORT_SIMULATE"] = "always"

    from app.main import app

    with TestClient(app) as c:
        yield c
