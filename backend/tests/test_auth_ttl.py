# -*- coding: utf-8 -*-
"""控制权限时长（TTL）语义回归。

阶段 7 需求：
- 获取管理权限默认**不限时**（TTL=0），直到后端重启 / 主动登出；
- 时长可在设置页由管理员调整（POST /api/settings/control-ttl），并持久化到 .env；
- 调整**不追溯**：已签发的令牌保持签发时的有效期；
- 无论设多长，令牌都只存在于后端进程内存 —— 重启后端必须重新获取权限
  （令牌存 `_TOKENS` 字典，进程没了自然清空，这是结构保证，无需测试）。
"""
from __future__ import annotations

import time

import pytest

from app.api import auth as auth_mod

PASSWORD = "test1234"


@pytest.fixture(autouse=True)
def _reset_ttl_state():
    """模块级全局状态（_TTL_SEC 与 _TOKENS）在测试间复位（坑 24）。

    conftest 的 client 是 session 级的，这里把时长钉回 0（不限时），
    避免本文件把 TTL 改成 60 秒后泄漏给别的测试文件。
    """
    auth_mod.set_ttl(0)
    auth_mod._TOKENS.clear()
    yield
    auth_mod.set_ttl(0)
    auth_mod._TOKENS.clear()


def _login(client):
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- 单元：时长 setter ----------------

def test_set_ttl_rejects_negative():
    with pytest.raises(ValueError):
        auth_mod.set_ttl(-1)


def test_set_ttl_caps_huge_values():
    assert auth_mod.set_ttl(10**9) == auth_mod.TTL_MAX
    auth_mod.set_ttl(0)


def test_set_ttl_zero_is_unlimited():
    assert auth_mod.set_ttl(0) == 0
    assert auth_mod.get_ttl() == 0


# ---------------- 单元：签发与校验 ----------------

def test_default_ttl_zero_issues_never_expiring_token():
    auth_mod.set_ttl(0)
    out = auth_mod.issue_token()
    assert out["expires_at"] is None
    assert out["ttl"] == 0
    assert auth_mod.token_role(out["token"]) == "admin"      # 永不因时间过期


def test_limited_ttl_issues_expiring_token():
    auth_mod.set_ttl(60)
    out = auth_mod.issue_token()
    assert out["expires_at"] is not None
    assert abs(out["expires_at"] - (time.time() + 60)) < 5
    assert auth_mod.token_role(out["token"]) == "admin"


def test_ttl_change_is_not_retroactive():
    """改时长不追溯：永久令牌在改成 60 秒后依然有效。"""
    auth_mod.set_ttl(0)
    permanent = auth_mod.issue_token()["token"]
    auth_mod.set_ttl(60)
    assert auth_mod.token_role(permanent) == "admin"


# ---------------- API：/auth/status 与 /settings/control-ttl ----------------

def test_status_reports_ttl(client):
    body = client.get("/api/auth/status").json()
    assert body["ttl"] == 0                      # autouse 夹具钉回 0
    assert body["control_active"] is False


def test_login_unlimited_returns_null_expires_at(client):
    d = _login(client)
    assert d["ttl"] == 0
    assert d["expires_at"] is None


def test_control_ttl_requires_admin(client):
    r = client.post("/api/settings/control-ttl", json={"ttl_sec": 1800})
    assert r.status_code == 401                  # 没令牌不给改


def test_control_ttl_updates_and_affects_new_tokens_only(client):
    d = _login(client)                           # 永久令牌
    old_token = d["token"]
    r = client.post("/api/settings/control-ttl",
                    json={"ttl_sec": 1800},
                    headers={"X-Control-Token": old_token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["ttl"] == 1800
    assert "新签发" in body["note"]

    # 旧永久令牌不被追溯
    assert client.get("/api/auth/status",
                      headers={"X-Control-Token": old_token}).json()["control_active"]

    # 新签发的令牌按新时长（≈1800s）
    new = client.post("/api/auth/login", json={"password": PASSWORD}).json()
    assert new["ttl"] == 1800
    assert abs(new["expires_at"] - (time.time() + 1800)) < 5


def test_logout_still_works_for_permanent_token(client):
    tok = _login(client)["token"]
    r = client.post("/api/auth/logout", headers={"X-Control-Token": tok})
    assert r.status_code == 200
    assert auth_mod.token_role(tok) is None
