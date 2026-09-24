# -*- coding: utf-8 -*-
"""阶段 5：点动（示教）引擎 + API 测试。

覆盖：鉴权门 / 增量点动 / 限位夹紧 / 连续点动 + 看门狗 / 急停与围栏拦截 / 急停停点动。

测试确定性技巧：点动基准姿态取自 motion.last_target（优先于实时采集），因此先用
/api/control/move 把引擎目标钉在 [0,0,0,0,0,0]，点动结果就可精确预测，
不受模拟采集漂移影响。
"""
from __future__ import annotations

import time

import pytest

import app.services.jog as jogmod

HOME = [0.0] * 6


@pytest.fixture(autouse=True)
def _reset_state():
    """点动/执行引擎/围栏互锁都是模块级全局状态，测试间必须复位，否则串味。"""
    from app.services import safety_guard
    from app.services.jog import jog
    from app.services.motion import motion

    def _clean():
        jog.stop("user")
        motion.reset_estop()
        motion.last_target = None
        safety_guard.update("safe", "", "", 0.0, 1.0)

    _clean()
    yield
    _clean()


def _h(client):
    r = client.post("/api/auth/login", json={"password": "test1234"})
    assert r.status_code == 200, r.text
    return {"X-Control-Token": r.json()["token"]}


def _home(client, h):
    """把执行引擎目标钉到原点，作为点动的确定性基准。"""
    r = client.post("/api/control/move", json={"joints": HOME}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True, r.text


# ---------------------------------------------------------------- 鉴权
def test_jog_requires_token(client):
    assert client.get("/api/control/jog").status_code == 401
    assert client.post("/api/control/jog/step",
                       json={"joint": 1, "dir": 1}).status_code == 401
    assert client.post("/api/control/jog/start",
                       json={"joint": 1, "dir": 1}).status_code == 401


# ---------------------------------------------------------------- 状态
def test_jog_state(client):
    r = client.get("/api/control/jog", headers=_h(client))
    assert r.status_code == 200
    d = r.json()
    assert d["active"] is False
    assert d["max_speed_dps"] == 30.0          # 来自 robot.yaml motion.jog.max_speed_dps
    assert d["mode"] == "sim"


# ---------------------------------------------------------------- 增量点动
def test_jog_step_moves_only_target_joint(client):
    h = _h(client)
    _home(client, h)
    r = client.post("/api/control/jog/step",
                    json={"joint": 2, "dir": 1, "angle_deg": 5, "speed_dps": 10},
                    headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["joint"] == 2 and d["dir"] == 1
    assert d["angle_deg"] == 5.0
    assert d["speed_dps"] == 10.0
    assert d["duration_ms"] == 500             # 5° / 10°/s = 0.5s
    # 只有 J2 从 0 走到 +5
    assert d["target"] == [0.0, 5.0, 0.0, 0.0, 0.0, 0.0]
    assert d["limit_clamped"] is False


def test_jog_step_negative_direction(client):
    h = _h(client)
    _home(client, h)
    d = client.post("/api/control/jog/step",
                    json={"joint": 4, "dir": -1, "angle_deg": 1, "speed_dps": 5},
                    headers=h).json()
    assert d["ok"] is True and d["dir"] == -1
    assert d["target"] == [0.0, 0.0, 0.0, -1.0, 0.0, 0.0]


def test_jog_step_speed_clamped_to_max(client):
    """请求 60°/s 应被夹到上限 30°/s。"""
    h = _h(client)
    _home(client, h)
    d = client.post("/api/control/jog/step",
                    json={"joint": 1, "dir": 1, "angle_deg": 10, "speed_dps": 60},
                    headers=h).json()
    assert d["ok"] is True
    assert d["speed_dps"] == 30.0


def test_jog_step_limit_clamp(client):
    """J1 上限 170°：从 0 一步 +180° 应夹到 170° 并给出警告。"""
    h = _h(client)
    _home(client, h)
    d = client.post("/api/control/jog/step",
                    json={"joint": 1, "dir": 1, "angle_deg": 180, "speed_dps": 30},
                    headers=h).json()
    assert d["ok"] is True
    assert d["limit_clamped"] is True
    assert d["target"][0] == 170.0
    assert d.get("warnings")


def test_jog_step_bad_joint(client):
    h = _h(client)
    assert client.post("/api/control/jog/step",
                       json={"joint": 7, "dir": 1, "angle_deg": 1},
                       headers=h).status_code == 422     # schema 校验
    assert client.post("/api/control/jog/step",
                       json={"joint": 1, "dir": 0, "angle_deg": 1},
                       headers=h).status_code == 400     # dir 语义校验


# ---------------------------------------------------------------- 连续点动
def test_jog_continuous_keepalive_stop(client):
    h = _h(client)
    _home(client, h)
    r = client.post("/api/control/jog/start",
                    json={"joint": 3, "dir": -1, "speed_dps": 8}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["active"] is True
    assert r.json()["joint"] == 3 and r.json()["dir"] == -1

    time.sleep(0.35)                            # 8°/s → 约 -2.8°
    st = client.get("/api/control/jog", headers=h).json()
    assert st["active"] is True
    assert st["target"][2] < -0.5               # J3 确实在往负方向走

    # keepalive 刷新看门狗
    k = client.post("/api/control/jog/keepalive", headers=h).json()
    assert k["ok"] is True and k["action"] == "keepalive"

    s = client.post("/api/control/jog/stop", headers=h).json()
    assert s["action"] == "stop"
    assert s["active"] is False
    assert s["stopped_motion"] is True


def test_jog_keepalive_without_motion(client):
    h = _h(client)
    k = client.post("/api/control/jog/keepalive", headers=h).json()
    assert k["ok"] is False and k["active"] is False


def test_jog_watchdog_auto_stop(client, monkeypatch):
    """死人开关：超过 watchdog_ms 未 keepalive → 自动停（对应示教器松开使能）。"""
    monkeypatch.setattr(jogmod, "_cfg",
                        lambda k, d: {"watchdog_ms": 150, "tick_ms": 40,
                                      "max_speed_dps": 30}.get(k, d))
    h = _h(client)
    _home(client, h)
    r = client.post("/api/control/jog/start",
                    json={"joint": 5, "dir": 1, "speed_dps": 5}, headers=h)
    assert r.json()["active"] is True
    time.sleep(0.5)
    st = client.get("/api/control/jog", headers=h).json()
    assert st["active"] is False
    assert st["reason"] == "watchdog"


# ---------------------------------------------------------------- 安全关
def test_jog_blocked_by_estop(client):
    h = _h(client)
    client.post("/api/control/estop", headers=h)
    r = client.post("/api/control/jog/step",
                    json={"joint": 1, "dir": 1, "angle_deg": 1, "speed_dps": 5},
                    headers=h)
    assert r.status_code == 409
    # 统一错误格式：HTTPException 的 detail 被中间件置空，原因在 message
    assert r.json()["code"] == "HTTP_ERROR"
    assert "急停" in r.json()["message"]


def test_jog_blocked_by_fence(client):
    from app.services import safety_guard
    safety_guard.update("danger", "z1", "危险区", 0.0, 0.05)
    h = _h(client)
    r = client.post("/api/control/jog/step",
                    json={"joint": 1, "dir": 1, "angle_deg": 1, "speed_dps": 5},
                    headers=h)
    assert r.status_code == 409
    assert r.json()["code"] == "HTTP_ERROR"
    assert "围栏" in r.json()["message"]


def test_estop_stops_running_jog(client):
    h = _h(client)
    _home(client, h)
    assert client.post("/api/control/jog/start",
                       json={"joint": 2, "dir": 1, "speed_dps": 5},
                       headers=h).json()["active"] is True
    client.post("/api/control/estop", headers=h)
    st = client.get("/api/control/jog", headers=h).json()
    assert st["active"] is False
    assert st["reason"] == "estop"
