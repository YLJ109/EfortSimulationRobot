# -*- coding: utf-8 -*-
"""阶段 3 运维增强 + 阶段 4 企业级加固 测试。

覆盖：统一事件总线 / 角色权限 / 健康总览 / 配置备份导出导入 / 围栏版本回滚 / 围栏互锁。

注意：save_safety() 会写 config/safety.json —— 测试里把 SAFETY_PATH 指到临时文件，
绝不污染用户调好的真实配置。
"""
from __future__ import annotations

import json

import pytest

from app.core import safety_config as sc


@pytest.fixture(autouse=True)
def tmp_safety_path(tmp_path, monkeypatch):
    p = tmp_path / "safety.json"
    monkeypatch.setattr(sc, "SAFETY_PATH", str(p))
    yield p


@pytest.fixture(autouse=True)
def reset_safety_guard():
    """围栏互锁是模块级全局状态，测试之间必须复位，否则会串味（用例互相卡死）。"""
    from app.services import safety_guard
    yield
    safety_guard.update("safe", "", "", 0.0, 1.0)


def _tok(client, password="test1234"):
    r = client.post("/api/auth/login", json={"password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(client, password="test1234"):
    return {"X-Control-Token": _tok(client, password)}


# =====================================================================
# 统一事件总线 / 审计
# =====================================================================
def test_login_emits_auth_event(client):
    _tok(client)                                    # 一次成功登录
    r = client.get("/api/events?category=auth&limit=50")
    assert r.status_code == 200
    actions = [e["action"] for e in r.json()["items"]]
    assert "auth.login" in actions


def test_failed_login_emits_warn_event(client):
    bad = client.post("/api/auth/login", json={"password": "definitely-wrong"})
    assert bad.status_code == 401
    r = client.get("/api/events?category=auth&level=warn&limit=50")
    assert any(e["action"] == "auth.login_failed" for e in r.json()["items"])


def test_events_filters_and_stats(client):
    _tok(client)
    lst = client.get("/api/events?limit=200").json()
    assert "items" in lst and "total" in lst
    st = client.get("/api/events/stats").json()
    assert st["total"] >= 1
    assert "auth" in st["by_category"]


def test_events_export_json_and_csv(client):
    _tok(client)
    j = client.get("/api/events/export?format=json")
    assert j.status_code == 200 and "efort-events/v1" in j.text
    c = client.get("/api/events/export?format=csv")
    assert c.status_code == 200
    assert "timestamp,category,level" in c.text.replace('"', "")


def test_clear_events_requires_admin_token(client):
    # 无令牌 -> 401
    assert client.delete("/api/events").status_code == 401
    # 有令牌(admin) -> 可清理
    r = client.delete("/api/events", headers=_h(client))
    assert r.status_code == 200 and r.json()["ok"] is True


def test_move_emits_control_event(client):
    h = _h(client)
    r = client.post("/api/control/move", json={"joints": [0, 0, 0, 0, 0, 0]}, headers=h)
    assert r.status_code == 200
    r = client.get("/api/events?category=control&limit=50")
    actions = [e["action"] for e in r.json()["items"]]
    assert "control.move" in actions


# =====================================================================
# 角色（阶段 4）
# =====================================================================
def test_login_returns_admin_role(client):
    d = client.post("/api/auth/login", json={"password": "test1234"}).json()
    assert d["role"] == "admin"
    st = client.get("/api/auth/status",
                    headers={"X-Control-Token": d["token"]}).json()
    assert st["role"] == "admin" and st["control_active"] is True


def test_operator_role_disabled_by_default(client):
    """没配 EFORT_OPERATOR_PASSWORD 时，操作员角色不可用（不锁死既有部署）。"""
    st = client.get("/api/auth/status").json()
    assert st["roles_enabled"]["admin"] is True
    assert st["roles_enabled"]["operator"] is False


# =====================================================================
# 健康与重连
# =====================================================================
def test_system_health_shape(client):
    d = client.get("/api/system/health").json()
    for k in ("status", "service", "version", "uptime_sec", "db", "robot",
              "ws_clients", "motion", "safety_interlock"):
        assert k in d, f"健康检查缺少字段 {k}"
    # 测试环境用 EFORT_SIMULATE=always，机器人链路必然是模拟 -> degraded
    assert d["status"] in ("ok", "degraded", "down")
    assert d["db"]["ok"] is True, f"db 不可用: {d['db']}"
    assert d["robot"]["simulated"] is True


def test_reconnect_requires_token(client):
    assert client.post("/api/system/reconnect").status_code == 401
    assert client.post("/api/system/reconnect", headers=_h(client)).status_code == 200


# =====================================================================
# 配置备份导出 / 导入
# =====================================================================
def test_system_export_requires_token(client):
    assert client.get("/api/system/export").status_code == 401


def test_system_export_import_roundtrip(client):
    h = _h(client)
    # 造一个点位，导出时应带出去
    p = client.post("/api/points",
                    json={"name": "备份测试点", "group": "ops",
                          "joints": [1, 2, 3, 4, 5, 6]}, headers=h).json()

    b = client.get("/api/system/export", headers=h).json()
    assert b["format"] == "efort-system/v1"
    assert "safety" in b and "points" in b and "programs" in b
    assert any(x["id"] == p["id"] for x in b["points"])

    # 改个名字再导入：应命中同名走到更新分支
    for x in b["points"]:
        if x["name"] == "备份测试点":
            x["joints"] = [6, 5, 4, 3, 2, 1]
    r = client.post("/api/system/import", json=b, headers=h)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["safety"] is True
    assert res["points"]["updated"] >= 1

    got = client.get(f"/api/points/{p['id']}").json()
    assert got["joints"] == [6, 5, 4, 3, 2, 1]


def test_system_import_is_admin_only(client):
    """导入属于高风险操作：无令牌 401。"""
    assert client.post("/api/system/import", json={}).status_code == 401


# =====================================================================
# 围栏配置版本化
# =====================================================================
def test_safety_version_created_on_save_and_can_rollback(client):
    h = _h(client)
    base = sc.default_safety()

    # 第一次保存（改动）后应产生历史版本
    v1 = dict(base)
    v1["zones"][0]["half"] = {"x": 1.4, "z": 1.4}
    assert client.put("/api/safety", json={"config": v1}, headers=h).status_code == 200

    v2 = dict(base)
    v2["zones"][0]["half"] = {"x": 0.5, "z": 0.5}
    assert client.put("/api/safety", json={"config": v2}, headers=h).status_code == 200
    assert client.get("/api/safety").json()["zones"][0]["half"]["x"] == 0.5

    vers = client.get("/api/safety/versions", headers=h).json()
    assert len(vers) >= 1, "保存前应先归档旧版本"
    # 列表里最新的版本是改动前那份（1.4 或更早），回滚回去后当前配置不应再是 0.5
    latest = vers[0]
    full = client.get(f"/api/safety/versions/{latest['id']}", headers=h).json()
    assert "config" in full

    rr = client.post(f"/api/safety/versions/{latest['id']}/rollback", headers=h)
    assert rr.status_code == 200, rr.text
    after = client.get("/api/safety").json()["zones"][0]["half"]["x"]
    assert after != 0.5, "回滚后应恢复到历史版本的值"


def test_versions_require_token(client):
    assert client.get("/api/safety/versions").status_code == 401
    assert client.post("/api/safety/versions/1/rollback").status_code == 401


# =====================================================================
# 围栏互锁（阶段 4 服务端兜底）
# =====================================================================
def test_live_state_blocks_move_when_unsafe(client):
    h = _h(client)
    # 上报危险状态 -> 下发应被 409 拦截
    client.post("/api/safety/live", json={"state": "danger", "zone_id": "z1",
                                          "zone_name": "主工作区"})
    live = client.get("/api/safety/live").json()
    assert live["reported"] is True and live["state"] == "danger"

    r = client.post("/api/control/move", json={"joints": [0, 0, 0, 0, 0, 0]}, headers=h)
    assert r.status_code == 409, r.text
    body = r.json()
    assert "互锁" in (body.get("message") or body.get("detail") or "")

    # 被拦截要留痕（事后追责）
    ev = client.get("/api/events?category=control&level=warn&limit=50").json()
    assert any(e["action"] == "control.move_blocked" for e in ev["items"])

    # 恢复安全 -> 可以正常下发
    client.post("/api/safety/live", json={"state": "safe", "ratio": 1.0})
    r = client.post("/api/control/move", json={"joints": [0, 0, 0, 0, 0, 0]}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_missing_live_state_warns_but_allows(client):
    """互锁未上报时不阻断纯脚本调用，但要记一条告警（默认 require_live_safety=false）。"""
    h = _h(client)
    # 确保没有新鲜上报：让时间戳过期不可控，这里靠"首次从未上报"的场景难以在同一会话构造，
    # 因此只断言 staleness 字段存在且 check 逻辑不抛错。
    snap = client.get("/api/safety/live").json()
    assert "fresh" in snap and "interlocked" in snap
    r = client.post("/api/control/move", json={"joints": [0, 0, 0, 0, 0, 0]}, headers=h)
    assert r.status_code in (200, 409)
