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


@pytest.fixture(autouse=True)
def _no_real_modbus(monkeypatch):
    """★★ 红线 R3（硬护栏）：测试期间禁止连真实控制器 Modbus ★★

    为什么必须有：`EFORT_SIMULATE=always` 只影响**运动引擎/采集器的降级决策**，
    它**不会**拦住 `ModbusRobot.read_regs/write_reg` —— 那些方法无条件对
    `config/robot.yaml` 里的 host:port 开 TCP socket（modbus.py 没有 simulate 短路）。
    于是：只要某个用例 monkeypatch 掉"真实下发总闸"（如 /api/ready 白名单测试），
    `_ready()` 就会去真机做「读快照 → 清报警 → 上伺服 → 加载 → 运行」。
    真事故：本机的 192.168.1.12:502 可达，红线段测试曾真的连上控制器。

    做法：把单例的 host/port 指向本机不可达端口 —— 任何漏网的调用都会**快速失败**
    （连接被拒），而不是去写真机。用例若需要"看着像能通"的控制器，请自行注入假对象
    （见 tests/test_safety_redline.py 的 _FakeMB / test_audit_p0.py 的 _MB）。
    """
    try:
        from app.services.motion import motion
        monkeypatch.setattr(motion.modbus, "host", "127.0.0.1", raising=False)
        monkeypatch.setattr(motion.modbus, "port", 1, raising=False)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_settings_overlay():
    """★ 跨文件状态污染兜底：robot.model 等配置来自 `get_config()` 单例 + 覆盖层文件。

    `test_settings.py::test_operator_can_write_non_admin_field` 会把
    `robot.model="ER8-700H-OP"` 写进 config/app_settings.json 并 reload 到全局
    Config 单例；若该文件在用例结束后残留（或被其它文件的用例读到），
    `/api/health`、`/api/meta` 等只读接口就会读到被污染的机型名而误报失败。

    这里在每个用例前后都清掉覆盖层文件并 reload 配置，保证任何用例都不继承
    上一个用例写下的差异。绝不碰 config/robot.yaml（只读合并来源）。
    """
    from app.core.config import project_root, reload_config
    from app.core.app_settings import SETTINGS_PATH

    def _clean():
        try:
            if os.path.isfile(SETTINGS_PATH):
                os.remove(SETTINGS_PATH)
        except OSError:
            pass
        try:
            reload_config()
        except Exception:
            pass

    _clean()
    yield
    _clean()
