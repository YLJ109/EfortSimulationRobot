# -*- coding: utf-8 -*-
"""
控制权限鉴权（阶段 1）。

设计：
  - 管理员密码在启动时从环境变量 EFORT_ADMIN_PASSWORD 或配置文件 auth.admin_password
    载入，并用 PBKDF2 哈希保存在内存（不落盘明文）。
  - 未配置密码时，启动时生成随机临时密码并打印到日志（提醒尽快设置），保证服务可起。
  - 前端用密码调 POST /api/auth/login 换取控制令牌（默认不限时，直到后端重启 /
    主动登出；可在设置页改为限时，或用 EFORT_CONTROL_TTL 设启动默认值）。
  - 所有"控制类"接口（/api/control/*）需在请求头带 X-Control-Token（或 Bearer），
    否则返回 401。监控类接口（WS 姿态、状态、安全围栏）不受影响，始终可读。
  - 令牌可主动 POST /api/auth/logout 释放；过期自动失效。

角色（阶段 4）：
  - admin    ：全能 —— 可执行点位/程序、修改安全围栏配置、导入系统备份、清理审计日志。
  - operator ：只能"操控机器人"（/api/control/move、急停复位等），无法改配置。
  - 两级口令分别来自 EFORT_ADMIN_PASSWORD / EFORT_OPERATOR_PASSWORD（或配置文件的
    auth.admin_password / auth.operator_password）。★ 未配置 operator 时只能用管理员登录，
    此时所有持有令牌者都是 admin —— 保持阶段 1 的既有行为，不会突然锁死已有部署。
  - 所有登录/登出/失败都写入统一事件总线（services/events.py）作为审计留痕。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.core.config import get_config
from app.services.events import emit as emit_event

log = logging.getLogger("auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

def _env_ttl() -> int:
    """读启动环境里的令牌时长。0 = 不限时（直到后端重启 / 主动登出）。

    ★ 上限 30 天：手滑填个天文数字时按"基本等于不限时"处理，同时把明显的
      负数/垃圾值归 0，而不是让后端起不来。
    """
    raw = str(os.environ.get("EFORT_CONTROL_TTL", "0")).strip()
    try:
        v = int(raw)
    except ValueError:
        v = 0
    return max(0, min(v, 2_592_000))


TTL_MAX = 2_592_000  # 30 天

# 启动默认值（.env / 环境变量可覆盖）。★ 运行时可由设置页调（set_ttl）。
CONTROL_TTL = _env_ttl()
_TTL_SEC: int = CONTROL_TTL


def get_ttl() -> int:
    """当前生效的令牌时长（秒）。0 = 不限时。"""
    return _TTL_SEC


def set_ttl(sec: int) -> int:
    """运行时调整令牌时长（设置页调，管理员）。0 = 不限时。

    ★ 只影响**之后新签发**的令牌 —— 已签发的令牌保持签发时的有效期。
      不追溯是为了避免"管理员刚调完时长，在线会话突然集体掉线"这种事故；
      想让现有限牌按新时长走，重新登出/登录即可。
    """
    global _TTL_SEC
    v = int(sec)
    if v < 0:
        raise ValueError("ttl_sec 不能为负数")
    _TTL_SEC = min(v, TTL_MAX)
    return _TTL_SEC

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"


def _hash_of(password: str) -> Tuple[bytes, bytes]:
    salt = secrets.token_bytes(16)
    return salt, hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)


def _load_role_hash(env_var: str, cfg_key: str, label: str,
                    required: bool) -> Optional[Tuple[bytes, bytes]]:
    """载入某个角色的口令哈希。required=True 时缺失会用临时随机密码兜底。"""
    cfg = get_config()
    pw = os.environ.get(env_var) or cfg.get("auth", cfg_key, default=None)
    if not pw:
        if not required:
            return None      # 可选角色：没配就是禁用
        pw = secrets.token_urlsafe(10)
        log.warning(
            "未配置%s密码(%s / auth.%s)，已生成临时密码: %s "
            "—— 请尽快通过环境变量或配置文件设置，否则任何人拿到该密码即可取得控制权限。",
            label, env_var, cfg_key, pw,
        )
    return _hash_of(pw)


# role -> (salt, hash)；operator 未配置时为 None（该角色禁用）
_ROLE_HASH: Dict[str, Optional[Tuple[bytes, bytes]]] = {
    ROLE_ADMIN: _load_role_hash("EFORT_ADMIN_PASSWORD", "admin_password", "管理员", True),
    ROLE_OPERATOR: _load_role_hash("EFORT_OPERATOR_PASSWORD", "operator_password", "操作员", False),
}

_TOKENS: dict = {}  # token -> {"exp": 过期时间戳(epoch 秒), "role": admin|operator}


def _role_from_password(password: str) -> Optional[str]:
    """判断密码属于哪个角色；都不匹配返回 None。管理员优先比对。"""
    for role in (ROLE_ADMIN, ROLE_OPERATOR):
        entry = _ROLE_HASH.get(role)
        if not entry:
            continue
        salt, expect = entry
        h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        if hmac.compare_digest(h, expect):
            return role
    return None


def issue_token(role: str = ROLE_ADMIN) -> dict:
    tok = secrets.token_hex(24)
    ttl = get_ttl()
    # ★ ttl=0 → 不限时（exp=None）：默认行为。令牌仍随后端重启消失（内存态），
    #   也可随时 POST /api/auth/logout 主动释放。
    exp = (time.time() + ttl) if ttl > 0 else None
    _TOKENS[tok] = {"exp": exp, "role": role}
    return {"token": tok, "expires_at": exp, "ttl": ttl, "role": role}


def revoke_token(tok: str) -> None:
    _TOKENS.pop(tok, None)


def token_role(tok: Optional[str]) -> Optional[str]:
    """返回令牌对应角色；无效/过期返回 None。exp=None 表示不限时。"""
    if not tok:
        return None
    rec = _TOKENS.get(tok)
    if not rec:
        return None
    if rec["exp"] is not None and rec["exp"] < time.time():
        _TOKENS.pop(tok, None)
        return None
    return rec.get("role")  # 缺 role=损坏记录，按无角色拒绝，不回退 admin


def valid_token(tok: Optional[str]) -> bool:
    return token_role(tok) is not None


# ---------------------------------------------------------------- 口令变更（阶段 6 设置页）
def set_password(role: str, password: Optional[str]) -> None:
    """设置/清除某个角色的口令（只改内存哈希；落盘由调用方负责）。

    `password=None` 或空串 = 禁用该角色（operator 支持，admin 不允许 —— 那会把自己锁在外面）。
    """
    if role not in (ROLE_ADMIN, ROLE_OPERATOR):
        raise ValueError("角色只能是 %s / %s" % (ROLE_ADMIN, ROLE_OPERATOR))
    _ROLE_HASH[role] = _hash_of(password) if password else None


def revoke_role(role: str, keep: Optional[str] = None) -> int:
    """作废某角色已签发的令牌，返回作废数量。

    ★ 为什么改口令要顺手作废令牌：令牌是"用旧口令换来的通行证"，口令换了却把旧通行证
      留着，改口令就只是心理安慰 —— 拿到旧口令的人仍然能继续控制机器人。这是安全问题，
      不是体验问题。
    `keep` 用于豁免调用者自己手里那一个：不然用户刚点完"修改密码"就被自己踢下线，
      分不清是改成功了还是服务坏了。
    """
    n = 0
    for t, rec in list(_TOKENS.items()):
        if rec.get("role") == role and t != keep:
            _TOKENS.pop(t, None)
            n += 1
    return n


def _extract_token(authorization: Optional[str], x_control_token: Optional[str]) -> Optional[str]:
    if x_control_token:
        return x_control_token
    if authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:].strip()
        return tok or None   # "Bearer " 后为空 → 视为未携带，不落入 valid_token("") 假阴性语义
    return None


def require_control(
    authorization: Optional[str] = Header(default=None),
    x_control_token: Optional[str] = Header(default=None, alias="X-Control-Token"),
) -> str:
    """控制类接口依赖：校验控制令牌，无效/缺失/过期返回 401。返回令牌字符串。"""
    tok = _extract_token(authorization, x_control_token)
    if not valid_token(tok):
        raise HTTPException(status_code=401, detail="需要管理员控制权限：请先获取控制令牌")
    return tok


def require_admin(
    authorization: Optional[str] = Header(default=None),
    x_control_token: Optional[str] = Header(default=None, alias="X-Control-Token"),
) -> str:
    """管理员接口依赖（阶段 4）：在 require_control 之上要求角色必须是 admin。

    用于"改配置、导备份、清审计"这类高风险操作 —— 操作员令牌在这里被拒(403)。
    """
    tok = require_control(authorization, x_control_token)
    role = token_role(tok)
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="该操作需要管理员权限（当前为操作员）")
    return tok


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    try:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return (request.client.host if request.client else "") or ""
    except Exception:
        return ""


class LoginIn(BaseModel):
    password: str


@router.get("/status")
def api_status(x_control_token: Optional[str] = Header(default=None, alias="X-Control-Token")):
    return {
        "required": True,
        "control_active": valid_token(x_control_token),
        "role": token_role(x_control_token),
        "roles_enabled": {r: bool(_ROLE_HASH.get(r)) for r in (ROLE_ADMIN, ROLE_OPERATOR)},
        "ttl": get_ttl(),          # 0 = 不限时（直到后端重启 / 主动登出）
    }


@router.post("/login")
def api_login(body: LoginIn, request: Request):
    ip = _client_ip(request)
    role = _role_from_password(body.password)
    if not role:
        emit_event("auth", "warn", "auth.login_failed",
                   "登录失败：密码错误", {"ip": ip}, actor="anonymous", ip=ip)
        raise HTTPException(status_code=401, detail="管理员密码错误")
    out = issue_token(role)
    emit_event("auth", "info", "auth.login",
               f"已获取控制令牌（{role}）", {"role": role}, actor=role, ip=ip)
    return out


@router.post("/logout")
def api_logout(request: Request, tok: str = Depends(require_control)):
    role = token_role(tok) or "unknown"
    ip = _client_ip(request)
    revoke_token(tok)
    emit_event("auth", "info", "auth.logout",
               f"已释放控制令牌（{role}）", {"role": role}, actor=role, ip=ip)
    return {"ok": True}
