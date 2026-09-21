# -*- coding: utf-8 -*-
"""API 集成测试（TestClient，模拟模式 + 临时数据库）。"""
from __future__ import annotations


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["robot"] == "ER8-700H"
    assert "connected" in data


def test_meta(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    data = r.json()
    assert data["robot"] == "ER8-700H"
    assert len(data["axes"]) == 6
    assert len(data["joint_limits"]) == 6


def test_pose(client):
    r = client.get("/api/pose")
    assert r.status_code == 200
    data = r.json()
    for k in ("j1", "j2", "j3", "j4", "j5", "j6"):
        assert k in data


def test_history(client):
    r = client.get("/api/history", params={"limit": 10})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_export_csv(client):
    r = client.get("/api/export/csv", params={"limit": 10})
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]


def test_control_limits(client):
    r = client.get("/api/control/limits")
    assert r.status_code == 200
    data = r.json()
    assert data["readonly"] is True
    assert len(data["joints"]) == 6


def test_recordings_crud(client):
    # 创建
    r = client.post("/api/recordings", json={
        "name": "测试录制", "source": "sim", "duration_ms": 100,
        "frames": [
            {"t": 0, "j1": 0, "j2": 0, "j3": 0, "j4": 0, "j5": 0, "j6": 0},
            {"t": 100, "j1": 1, "j2": 0, "j3": 0, "j4": 0, "j5": 0, "j6": 0},
        ],
    })
    assert r.status_code == 200
    rid = r.json()["id"]

    # 列表包含
    r = client.get("/api/recordings")
    assert any(x["id"] == rid for x in r.json())

    # 详情
    r = client.get(f"/api/recordings/{rid}")
    assert r.status_code == 200
    assert len(r.json()["frames"]) == 2

    # 重命名
    r = client.put(f"/api/recordings/{rid}", json={"name": "改名"})
    assert r.json()["name"] == "改名"

    # 导出
    r = client.get(f"/api/recordings/{rid}/export")
    assert r.status_code == 200
    assert r.json()["format"] == "efort-recording/v1"

    # 删除
    r = client.delete(f"/api/recordings/{rid}")
    assert r.json()["ok"] is True

    # 删除后 404
    r = client.get(f"/api/recordings/{rid}")
    assert r.status_code == 404


def test_control_preview_joint_ok(client):
    r = client.post("/api/control/preview", json={
        "mode": "joint", "joints": [10, 0, 0, 0, 0, 0], "current": [0, 0, 0, 0, 0, 0],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert len(data["trajectory"]) > 0


def test_control_preview_joint_violation(client):
    r = client.post("/api/control/preview", json={
        "mode": "joint", "joints": [999, 0, 0, 0, 0, 0], "current": [0, 0, 0, 0, 0, 0],
    })
    data = r.json()
    assert len(data["violations"]) >= 1
    assert data["ok"] is False


def test_control_ik(client):
    r = client.post("/api/control/ik", json={
        "tcp": {"x": 300, "y": 0, "z": 700},
        "current": [0, 0, 0, 0, 0, 0],
        "keep_orientation": False,
    })
    assert r.status_code == 200
    data = r.json()
    assert "joints" in data
    assert len(data["joints"]) == 6
    assert data["readonly"] is True


def test_reconnect(client):
    r = client.post("/api/reconnect")
    assert r.status_code == 200
    data = r.json()
    assert "connected" in data
    assert "simulated" in data
