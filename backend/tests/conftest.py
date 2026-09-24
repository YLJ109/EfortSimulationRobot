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

# ★★ 口令必须与开发者本机的 .env 解耦 ★★
# 测试模块（如 test_ops.py / test_safety_api.py）会在导入期就 import app.core.* ，
# 从而触发 .env 加载；若此时环境变量还没设好，本机 .env 里的管理员口令会把测试
# 用的固定口令顶掉，导致"明明代码没问题却全部 401"。
# 本文件由 pytest 最先导入，因此在这里强制赋值最可靠。
os.environ["EFORT_ADMIN_PASSWORD"] = "test1234"
os.environ["EFORT_SIMULATE"] = "always"
# ★★ 真实下发总闸必须在测试环境强制关闭 ★★
# 开发者本机 .env 可能为了真机联调把 EFORT_REAL_MOTION 设为 1（robot.yaml real_write=true），
# 若测试进程继承它，任何 /control/move、/control/jog 测试都会**真的写控制器寄存器** ——
# 测试绝不允许动机器人。这里在导入任何 app 模块之前强制置 0。
os.environ["EFORT_REAL_MOTION"] = "0"


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    db_file = tmp_path_factory.mktemp("testdb") / "test.db"
    os.environ["EFORT_DB_URL"] = f"sqlite:///{db_file}"
    os.environ["EFORT_SIMULATE"] = "always"
    # 固定管理员密码, 供控制类接口(阶段1 鉴权门)测试登录（模块级已设，这里再兜一次）
    os.environ["EFORT_ADMIN_PASSWORD"] = "test1234"

    from app.main import app

    with TestClient(app) as c:
        yield c
