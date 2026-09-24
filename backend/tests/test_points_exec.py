# -*- coding: utf-8 -*-
"""阶段 2: 点位/程序 CRUD + 执行引擎鉴权门 + 急停 测试。"""
import os

os.environ["EFORT_ADMIN_PASSWORD"] = "test1234"  # 测试用固定管理员密码

from fastapi.testclient import TestClient


def _token(c: TestClient) -> str:
    r = c.post("/api/auth/login", json={"password": "test1234"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_points_crud(client: TestClient):
    # ★ 阶段 4：写操作需要控制令牌（点位决定机器人往哪走，不能谁都能改）
    h = {"X-Control-Token": _token(client)}
    # 创建
    r = client.post("/api/points", json={"name": "Home", "group": "home",
                                         "kind": "joint", "joints": [0, 0, 0, 0, 0, 0]},
                    headers=h)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    # 列表含该点（读操作仍然公开）
    lst = client.get("/api/points").json()
    assert any(p["id"] == pid for p in lst)
    # 更新
    r = client.put(f"/api/points/{pid}", json={"name": "Home2", "joints": [10, 20, 30, 40, 50, 60]},
                   headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "Home2"
    # 导出
    r = client.get(f"/api/points/{pid}/export")
    assert r.status_code == 200 and "efort-point/v1" in r.text
    # 删除
    r = client.delete(f"/api/points/{pid}", headers=h)
    assert r.status_code == 200
    assert client.get(f"/api/points/{pid}").status_code == 404


def test_points_crud_requires_token(client: TestClient):
    """无令牌时点位写操作必须被拒；读操作仍然开放。"""
    assert client.post("/api/points", json={"name": "NoAuth"}).status_code == 401
    assert client.get("/api/points").status_code == 200


def test_points_import(client: TestClient):
    h = {"X-Control-Token": _token(client)}
    r = client.post("/api/points/import", json={"name": "P1", "joints": [1, 2, 3, 4, 5, 6]},
                    headers=h)
    assert r.status_code == 200 and r.json()["joints"] == [1, 2, 3, 4, 5, 6]


def test_programs_crud(client: TestClient):
    h = {"X-Control-Token": _token(client)}
    # 先建一个点
    p = client.post("/api/points", json={"name": "A", "joints": [0] * 6}, headers=h).json()
    r = client.post("/api/programs", json={"name": "Prog1",
                                           "items": [{"point_id": p["id"], "speed_pct": 50}]},
                    headers=h)
    assert r.status_code == 200, r.text
    prid = r.json()["id"]
    assert r.json()["items_count"] == 1
    # 更新
    r = client.put(f"/api/programs/{prid}", json={"name": "Prog2"}, headers=h)
    assert r.json()["name"] == "Prog2"
    # 导出
    assert "efort-program/v1" in client.get(f"/api/programs/{prid}/export").text
    # 删除
    assert client.delete(f"/api/programs/{prid}", headers=h).status_code == 200


def test_exec_gate_requires_token(client: TestClient):
    # 无令牌 -> 401
    assert client.post("/api/control/move", json={"joints": [0, 0, 0, 0, 0, 0]}).status_code == 401
    assert client.post("/api/control/estop").status_code == 401


def test_exec_move_and_estop(client: TestClient):
    tok = _token(client)
    h = {"X-Control-Token": tok}
    # 执行(模拟模式): 返回 ok + 模式 sim
    r = client.post("/api/control/move", json={"joints": [0, 0, 0, 0, 0, 0]}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["mode"] == "sim"
    # 状态
    st = client.get("/api/control/state", headers=h).json()
    assert st["mode"] == "sim" and st["stopped"] is False
    # 急停
    assert client.post("/api/control/estop", headers=h).status_code == 200
    st = client.get("/api/control/state", headers=h).json()
    assert st["stopped"] is True
    # 急停后执行被拒
    r = client.post("/api/control/move", json={"joints": [0, 0, 0, 0, 0, 0]}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is False and r.json().get("estop") is True
    # 复位
    assert client.post("/api/control/estop/reset", headers=h).status_code == 200
    assert client.get("/api/control/state", headers=h).json()["stopped"] is False


def test_exec_limits(client: TestClient):
    tok = _token(client)
    h = {"X-Control-Token": tok}
    # 超出 J2 限位(上界 90) -> 拒绝
    r = client.post("/api/control/move", json={"joints": [0, 200, 0, 0, 0, 0]}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is False
