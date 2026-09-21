# -*- coding: utf-8 -*-
"""安全围栏配置 + 报警事件 API 测试。

注意：save_safety() 会写 config/safety.json。测试里把 SAFETY_PATH 指到临时文件，
绝不能污染用户调好的真实配置。
"""
from __future__ import annotations

import pytest

from app.core import safety_config as sc


@pytest.fixture(autouse=True)
def tmp_safety_path(tmp_path, monkeypatch):
    p = tmp_path / "safety.json"
    monkeypatch.setattr(sc, "SAFETY_PATH", str(p))
    yield p


def test_get_default(client):
    r = client.get("/api/safety")
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is True
    assert len(d["zones"]) == 1
    assert d["zones"][0]["shape"] == "rect"
    assert d["zones"][0]["half"] == {"x": 0.82, "z": 0.82}


def test_put_and_persist(client):
    cfg = sc.default_safety()
    cfg["zones"][0]["half"] = {"x": 1.10, "z": 0.95}
    cfg["zones"][0]["thresholds"]["warn"] = 0.45
    r = client.put("/api/safety", json={"config": cfg})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    got = client.get("/api/safety").json()
    assert got["zones"][0]["half"]["x"] == 1.10
    assert got["zones"][0]["thresholds"]["warn"] == 0.45


def test_put_rejects_bad_thresholds(client):
    cfg = sc.default_safety()
    cfg["zones"][0]["thresholds"]["warn"] = 0.10
    cfg["zones"][0]["thresholds"]["danger"] = 0.50   # danger 必须 < warn
    r = client.put("/api/safety", json={"config": cfg})
    assert r.status_code == 400
    assert r.json()["code"] == "SAFETY_CONFIG_INVALID"
    assert "危险阈值" in r.json()["message"]


def test_put_rejects_bad_color(client):
    cfg = sc.default_safety()
    cfg["zones"][0]["colors"]["safe"] = "green"      # 必须 #RRGGBB
    r = client.put("/api/safety", json={"config": cfg})
    assert r.status_code == 400


def test_put_rejects_bad_shape(client):
    cfg = sc.default_safety()
    cfg["zones"][0]["shape"] = "triangle"
    r = client.put("/api/safety", json={"config": cfg})
    assert r.status_code == 400


def test_circle_zone_roundtrip(client):
    cfg = sc.default_safety()
    cfg["zones"][0]["shape"] = "circle"
    cfg["zones"][0]["radius"] = 1.25
    r = client.put("/api/safety", json={"config": cfg})
    assert r.status_code == 200
    got = client.get("/api/safety").json()
    assert got["zones"][0]["shape"] == "circle"
    assert got["zones"][0]["radius"] == 1.25


def test_quad_zone_roundtrip(client):
    cfg = sc.default_safety()
    cfg["zones"][0]["shape"] = "quad"
    cfg["zones"][0]["corners"] = [[0, 1], [1, 0], [0, -1], [-1, 0]]
    r = client.put("/api/safety", json={"config": cfg})
    assert r.status_code == 200
    assert client.get("/api/safety").json()["zones"][0]["shape"] == "quad"


def test_reset(client):
    cfg = sc.default_safety()
    cfg["zones"][0]["half"]["x"] = 2.5
    client.put("/api/safety", json={"config": cfg})
    r = client.post("/api/safety/reset")
    assert r.status_code == 200
    assert r.json()["config"]["zones"][0]["half"]["x"] == 0.82


def test_events_crud(client):
    r = client.post("/api/safety/events", json={
        "zone_id": "z1", "zone_name": "主工作区", "state": "hit",
        "clearance": -0.031, "ratio": 0.0,
    })
    assert r.status_code == 200
    lst = client.get("/api/safety/events").json()
    assert len(lst) >= 1
    assert lst[0]["state"] == "hit"
    assert lst[0]["zone_name"] == "主工作区"

    d = client.delete("/api/safety/events")
    assert d.json()["ok"] is True
    assert client.get("/api/safety/events").json() == []


def test_events_rejects_bad_state(client):
    r = client.post("/api/safety/events", json={"state": "boom"})
    assert r.status_code == 400
    assert r.json()["code"] == "BAD_SAFETY_STATE"


def test_validate_only(client):
    cfg = sc.default_safety()
    cfg["alarm"]["banner_min"] = "nope"
    r = client.post("/api/safety/validate", json={"config": cfg})
    assert r.status_code == 400
