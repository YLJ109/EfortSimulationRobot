# -*- coding: utf-8 -*-
"""
系统设置 API（阶段 6）：`config/app_settings.json` 覆盖层的读写 + 生效 + 重置。

    GET  /api/settings           分组 + 字段元数据 + 生效值 + 来源（★ 公开读）
    GET  /api/settings/live      实时设备/连接信息（机器人、相机服务、相机、本服务、库）
    PUT  /api/settings           保存补丁（控制令牌；**逐字段**判定管理员）
    POST /api/settings/apply     执行生效动作：reload / reconnect
    POST /api/settings/test      连通性测试（机器人 / 相机服务），可带临时地址试连
    POST /api/settings/reset     ★ 重置所有参数（管理员，回到 robot.yaml 出厂值）
    POST /api/settings/password  修改管理员/操作员口令（管理员）
    POST /api/settings/control-ttl  控制权限时长（管理员；0=不限时），持久化到 .env

## 权限模型（与 auth.py 的角色严格对齐）

读接口全公开 —— 现场排查时"我改的是哪个值、现在到底生效的是谁"必须随时能看到，
把读也锁上只会让人去找配置文件原文，反而更容易改错。

写接口走两档：
  - `require_control`(401)：没令牌连入口都不给；
  - **逐字段**判管理员：`SCHEMA` 里 `admin=True` 的项，操作员令牌改会 403，且**整批拒绝**
    （不是"能改的改了、不能改的静默丢掉" —— 静默丢一半比全拒更容易出事）。

## 为什么"重置所有参数"是删文件而不是把值写回去

覆盖层只存"与出厂值不同的项"。所以重置 = 把 `app_settings.json` 删掉，
所有字段自然回到 `robot.yaml`。写成"逐字段把出厂值写回覆盖层"的话，
覆盖层就不再稀疏，以后想判断"这项是不是被改过"就无从下手了。

## ★ 本模块不碰机器人运动

设置页能改的是"参数"，不是"动作"。全文件没有任何 `motion.command` 调用 ——
改完参数只是让后续的操控用新参数，不会因为点了一次保存就动一下机器人。
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    _extract_token,
    require_admin,
    require_control,
    revoke_role,
    set_password,
    set_ttl,
    token_role,
    valid_token,
)
from app.api.system import _camera_link, _robot_link, _tcp_probe, invalidate_link_cache
from app.core import app_settings as st
from app.core.brand import SERVICE_NAME, SERVICE_VERSION
from app.core.config import CONFIG_PATH, db_path, get_config, project_root, reload_config
from app.services.collector import collector
from app.services.events import emit as emit_event

router = APIRouter(prefix="/api/settings", tags=["settings"])

_START = time.time()


def _actor(tok: str) -> str:
    return token_role(tok) or "unknown"


def _client_ip(request: Optional[Request]) -> str:
    try:
        fwd = request.headers.get("x-forwarded-for") if request else None
        if fwd:
            return fwd.split(",")[0].strip()
        return (request.client.host if request and request.client else "") or ""
    except Exception:
        return ""


def optional_control(
    authorization: Optional[str] = Header(default=None),
    x_control_token: Optional[str] = Header(default=None, alias="X-Control-Token"),
) -> Optional[str]:
    """可选令牌：有且有效就返回，没有/过期返回 None（**不报 401**）。

    用于读接口：要公开可读，但知道"来者是谁"才能告诉前端哪些格子该置灰。
    """
    tok = _extract_token(authorization, x_control_token)
    return tok if valid_token(tok) else None


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
@router.get("")
def api_describe(tok: Optional[str] = Depends(optional_control)):
    """配置全量描述（公开）。`role` 让前端把当前令牌改不了的项直接置灰。"""
    desc = st.describe()
    return {
        "ok": True,
        "role": token_role(tok) or "",
        "groups": desc["groups"],
        "apply_order": desc["apply_order"],
        "overlay_path": desc["overlay_path"],
        "overlay_keys": desc["overlay_keys"],
        "overridden": desc["overridden"],
        "runtime": st.runtime_flags(),
    }


@router.get("/live")
def api_live():
    """实时设备与连接信息（公开）：一排"现在到底是什么"的事实，不是可改的参数。

    ★ 与 `/api/settings` 分开：那边是"配置长什么样"，这边是"此刻连上了什么"。
      合成一个接口会让"保存后有没有生效"变得无法判断（值和状态混在一份响应里）。
    """
    cfg = get_config()
    conn = cfg.connection or {}
    mb = cfg.modbus or {}
    host = mb.get("host") or conn.get("host", "")
    port = int(mb.get("port") or conn.get("port", 502) or 502)
    sim_mode = str(conn.get("simulate") or "auto")

    robot = _robot_link(host, port, sim_mode)
    cam = _camera_link()

    # 网口可达性：短超时，只握手
    net_ok, net_err = _tcp_probe(host, port)

    db_ok, db_err, db_size = False, "", 0
    try:
        p = db_path()
        db_ok = os.path.isfile(p)
        db_size = os.path.getsize(p) if db_ok else 0
    except Exception as e:
        db_err = str(e)[:80]

    srv = cfg.server or {}
    return {
        "ok": True,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "service": {
            "name": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "host": srv.get("host", ""),
            "port": srv.get("port", 8000),
            "ws_path": srv.get("ws_path", "/ws/pose"),
            "uptime_sec": int(time.time() - _START),
            "python": __import__("platform").python_version(),
        },
        "robot": {
            **robot,
            "net_ok": bool(net_ok),
            "net_error": net_err,
            "unit_id": conn.get("unit_id", 1),
            "model": cfg.get("robot", "model", default=""),
            "serial": cfg.get("robot", "serial", default=""),
            "payload_kg": cfg.get("robot", "payload_kg"),
            "reach_mm": cfg.get("robot", "reach_mm"),
            "mounting": cfg.get("mounting", "type", default=""),
            "register": {"func": mb.get("func"), "base_addr": mb.get("base_addr"),
                         "count": mb.get("count"), "unit": mb.get("unit"),
                         "word_order": mb.get("word_order")},
        },
        "camera": {**cam, "env_url": os.environ.get("EFORT_CAMERA_URL", "")},
        "database": {"ok": db_ok, "path": db_path() if db_ok else "",
                     "size_kb": round(db_size / 1024.0, 1), "error": db_err,
                     "url": cfg.get("database", "url", default="")},
        "runtime": st.runtime_flags(),
        "paths": {"config": CONFIG_PATH, "settings": st.SETTINGS_PATH,
                  "project_root": project_root()},
    }


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
class PatchIn(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict, description="点号路径 → 值；值传 null = 恢复该项出厂值")


def _guard_admin_fields(values: Dict[str, Any], role: str) -> None:
    """逐字段权限判定。整批拒绝，不做部分应用。"""
    idx = st.field_index()
    locked = [k for k in values if (idx.get(k) or {}).get("admin", True)]
    if locked and role != ROLE_ADMIN:
        labels = [((idx.get(k) or {}).get("label") or k) for k in locked]
        raise HTTPException(
            403,
            detail="以下参数需要管理员权限：%s（请用管理员口令登录后再改）" % "、".join(labels[:6]),
        )


def _drop_caches() -> None:
    """改完配置要把"读过的旧值"清掉。

    有两层缓存：`Config` 是长驻对象（不 reload 就一直是旧值），
    相机的地址与链路探测各有几秒缓存（不清就表现为"保存了没生效，过一会儿又好了"）。
    """
    reload_config()
    try:
        from app.services import camera_client
        camera_client.invalidate()
    except Exception:
        pass
    try:
        invalidate_link_cache()
    except Exception:
        pass


def _apply_actions(actions: List[str], tok: str, reason: str) -> Dict[str, Any]:
    """执行生效动作。返回实际做了什么（供界面显示"已生效 / 需重启"）。"""
    done: List[str] = []
    notes: List[str] = []
    res: Dict[str, Any] = {}
    for a in actions:
        if a == "live":
            done.append("live")
        elif a == "reload":
            _drop_caches()
            done.append("reload")
        elif a == "reconnect":
            r = collector.reconnect()
            res["reconnect"] = r
            done.append("reconnect")
            if not r.get("connected"):
                notes.append("重连未成功，仍处于模拟/离线（检查网线、示教器档位与伺服使能）")
        elif a == "restart":
            # 后端不能重启自己（重启指令一旦发出去，响应就发不回来了）。
            done.append("restart")
            notes.append("该参数需要**重启后端进程**才会生效（改端口/监听地址/数据库/真实下发开关都属于这类）")
    if done:
        emit_event("config", "info", "config.settings_apply",
                   "配置生效动作已执行：%s" % "、".join(done),
                   {"actions": done, "reason": reason, **res}, actor=_actor(tok))
    return {"actions": done, "notes": notes, **res}


@router.put("")
def api_put(body: PatchIn, request: Request, tok: str = Depends(require_control)):
    """保存配置补丁。返回 changes / 需要哪些生效动作 / 是否已立即执行。

    ★ 默认 `apply=false`：只落盘不动运行时，界面上再点「立即生效」。
      为什么不自动执行：改 `connection.host` 会触发一次真实的断连重连 ——
      万一地址填错，现场会看到"保存一下就掉线了"，搞不清是保存导致还是网线松了。
      分成两步，出错时能一眼看出是哪一步。
    """
    role = token_role(tok) or "unknown"
    _guard_admin_fields(body.values, role)

    try:
        out = st.save_patch(body.values)
    except st.SettingsError as e:
        raise HTTPException(400, detail=str(e))
    except OSError as e:
        raise HTTPException(500, detail="配置写入失败：%s" % e)

    # 只落盘还不够：Config 是长驻对象，不 reload 的话"保存了但界面上还是旧值"。
    # 保存这一步就把缓存清掉；真正断开/重连控制器留给 /apply（见上面的 docstring）。
    _drop_caches()

    labels = {f["key"]: f["label"] for f in st.field_index().values()}
    changed = [
        {"key": k, "label": labels.get(k, k), "old": v["old"], "new": v["new"]}
        for k, v in out["changed"].items()
    ]
    if changed:
        emit_event("config", "warn", "config.settings_save",
                   "配置已修改：%s" % "、".join(c["label"] for c in changed[:8]),
                   {"changed": changed, "apply": out["apply"]},
                   actor=role, ip=_client_ip(request))

    return {
        "ok": True,
        "changed": changed,
        "apply": out["apply"],
        "needs_restart": "restart" in out["apply"],
        "overridden": len(st._flatten(out["values"] or {})),
        "note": "" if changed else "没有变化（提交的值与当前生效值相同）",
    }


class ApplyIn(BaseModel):
    actions: List[str] = Field(default_factory=list)


@router.post("/apply")
def api_apply(body: ApplyIn, request: Request, tok: str = Depends(require_control)):
    """立即生效。`actions` 为空时按当前覆盖层需要的动作自动推断。

    ★ 重新读盘是必须的：`Config` 是长驻对象，不 reload 的话"保存了但界面上还是旧值"。
    """
    acts = [a for a in (body.actions or []) if a in ("live", "reload", "reconnect", "restart")]
    if not acts:
        # 没指定就全做一遍轻量动作；restart 永远不由这里代劳
        acts = ["reload", "reconnect"]
    return {"ok": True, **_apply_actions(acts, tok, "manual"), "time": time.strftime("%H:%M:%S")}


class TestIn(BaseModel):
    target: str = Field(..., description="robot | camera")
    host: Optional[str] = Field(default=None, max_length=64)
    port: Optional[int] = Field(default=None, ge=1, le=65535)


@router.post("/test")
def api_test(body: TestIn, tok: str = Depends(require_control)):
    """连通性测试：**先用临时地址试，再决定要不要保存**。

    现场最常见的动作是"我猜是 192.168.1.13，先试试通不通"。没有这个接口的话，
    用户只能先保存再重连，试错了还得改回来，而每次保存都在改真实配置。
    """
    cfg = get_config()
    tgt = str(body.target or "").strip().lower()

    if tgt == "robot":
        conn = cfg.connection or {}
        mb = cfg.modbus or {}
        host = str(body.host or mb.get("host") or conn.get("host") or "")
        try:
            port = int(body.port or mb.get("port") or conn.get("port") or 502)
        except (TypeError, ValueError):
            return {"ok": False, "target": tgt, "error": "端口不是数字"}
        if not host:
            return {"ok": False, "target": tgt, "error": "未配置控制器地址"}
        # ★ P1-B8：「测试连接」是用户主动触发，必须绕过 3s 缓存 ——
        #   否则改完地址点测试，拿到的还是 3 秒前旧地址的结论（"改了没生效"）。
        ok, err = _tcp_probe(host, port, timeout=1.2, use_cache=False)
        # 网口通 ≠ Modbus 通：顺带把当前采集器状态一并回传，避免"ping 通了就以为好了"
        return {
            "ok": bool(ok), "target": tgt, "host": host, "port": port,
            "level": "ok" if ok else "err",
            "message": ("TCP 可达（%s:%d）" % (host, port)) if ok
                       else ("TCP 不可达：%s" % (err or "超时")),
            "hint": "" if ok else "确认控制器已上电、网线接在控制器网口、工控机与控制器同网段",
            "collector": {"connected": bool(collector.connected),
                          "simulated": bool(collector.simulated)},
        }

    if tgt == "camera":
        from app.services import camera_client
        host = str(body.host or "").strip()
        port = body.port
        base = camera_client.base_url()
        if host and port:
            base = "http://%s:%d" % (host, int(port))
        t0 = time.time()
        code, data, err = camera_client.get_json("/status", timeout=5.0, base=base)
        ms = int((time.time() - t0) * 1000)
        if code != 200 or not isinstance(data, dict):
            return {"ok": False, "target": tgt, "base": base, "ms": ms, "level": "err",
                    "message": "连不上相机服务：%s" % (err or ("HTTP %s" % code)),
                    "hint": "确认相机服务进程已启动（run.bat camera），且端口与这里一致"}
        dev = data.get("device") or {}
        return {
            "ok": True, "target": tgt, "base": base, "ms": ms, "level": "ok",
            "message": "相机服务可达（%d ms）" % ms,
            "hint": "",
            "service": {"opened": bool(data.get("opened")), "port": data.get("port"),
                        "ai_enabled": bool(data.get("ai_enabled")),
                        "resolution": data.get("resolution"), "fps": data.get("fps")},
            "device": {"model": dev.get("model", ""), "serial": dev.get("serial", ""),
                       "ip": dev.get("ip", "")},
            "error": str(data.get("error") or "")[:160],
        }

    raise HTTPException(400, detail="target 只能是 robot / camera（收到 %s）" % body.target)


class ResetIn(BaseModel):
    confirm: bool = Field(default=False, description="必须显式传 true，防止误触")


@router.post("/reset")
def api_reset(body: ResetIn, request: Request, tok: str = Depends(require_admin)):
    """★ 重置所有参数：清空覆盖层，全部回到 `config/robot.yaml` 的出厂值。

    两道保险：
      1. `confirm=true` 必填 —— 这是不可撤销操作，按钮上点一下不该就能执行；
      2. 管理员令牌 —— 与"改围栏配置"同一档（回退到出厂值同样会改变机器人行为）。

    ★ 它是可恢复的：重置前的覆盖层内容会写进审计事件详情，需要时可以照着填回去。
    """
    if not body.confirm:
        raise HTTPException(400, detail="重置是不可撤销操作，请在界面上勾选确认后再提交")

    before = st.load_overlay()
    out = st.reset_settings()
    _drop_caches()

    emit_event("config", "critical", "config.settings_reset",
               "已重置所有参数，全部恢复出厂值（%d 项）" % len(out["removed"]),
               {"removed": out["removed"], "before": before},
               actor=_actor(tok), ip=_client_ip(request))
    return {
        "ok": True,
        "removed": out["removed"],
        "count": len(out["removed"]),
        "note": "已全部恢复为 config/robot.yaml 中的出厂值；"
                "监听地址/端口/数据库/真实下发开关这类参数需要重启后端才生效",
    }


class PasswordIn(BaseModel):
    role: str = Field(default=ROLE_ADMIN, description="admin | operator")
    password: str = Field(..., min_length=4, max_length=128)
    current: str = Field(..., description="当前该角色口令，用于二次确认")


def _env_path() -> str:
    return os.path.join(project_root(), ".env")


def _write_env_key(key: str, value: str) -> str:
    """把 KEY=VALUE 写进项目根 .env（保留其它行与注释，原子替换）。

    ★ 只做"整行替换或追加"，不解析、不重排、不删注释 —— 用户的 .env 可能手写过
      说明与其它变量，程序回写一次就抹掉是最让人恼火的事。
    """
    path = _env_path()
    lines: List[str] = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    out: List[str] = []
    hit = False
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == key:
            out.append("%s=%s" % (key, value))
            hit = True
        else:
            out.append(ln)
    if not hit:
        out.append("%s=%s" % (key, value))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")
    os.replace(tmp, path)
    return path


@router.post("/password")
def api_password(body: PasswordIn, request: Request, tok: str = Depends(require_admin)):
    """修改口令（管理员）。

    校验顺序很重要：**先验当前口令**，验过了才写。反过来的话，一个被临时授权的人
    可以靠"改口令"把自己永久留下 —— 这是越权，不是改名。
    """
    # ★ 审计修复 P1-E13：这里原本有一句 `cfg = get_config()` —— 赋了值却从没用过
    #   （口令走的是 env 直读，不经过 config）。留着会让人误以为配置表参与了口令校验。
    role = str(body.role or ROLE_ADMIN).strip().lower()
    if role not in (ROLE_ADMIN, ROLE_OPERATOR):
        raise HTTPException(400, detail="角色只能是 admin / operator")

    if role == ROLE_ADMIN:
        env_key = "EFORT_ADMIN_PASSWORD"
    else:
        env_key = "EFORT_OPERATOR_PASSWORD"

    # 用当前口令换出该角色，验证通过才允许改
    import hashlib
    import hmac

    from app.api.auth import _ROLE_HASH

    entry = _ROLE_HASH.get(role)
    if entry:
        salt, expect = entry
        h = hashlib.pbkdf2_hmac("sha256", body.current.encode("utf-8"), salt, 100_000)
        if not hmac.compare_digest(h, expect):
            emit_event("auth", "warn", "auth.password_failed",
                       "口令修改被拒：当前口令校验失败（%s）" % role,
                       {"role": role}, actor=_actor(tok), ip=_client_ip(request))
            raise HTTPException(403, detail="当前口令不正确，未做任何修改")
    elif role == ROLE_OPERATOR:
        # 未配置操作员口令时 entry 为 None：只有管理员能"首次设置"它
        pass

    set_password(role, body.password)
    revoke_role(role, keep=tok)
    try:
        path = _write_env_key(env_key, body.password)
        persisted = True
        perr = ""
    except OSError as e:
        path, persisted, perr = _env_path(), False, str(e)[:120]

    emit_event("auth", "critical", "auth.password_changed",
               "%s 口令已修改" % ("管理员" if role == ROLE_ADMIN else "操作员"),
               {"role": role, "persisted": persisted, "path": path},
               actor=_actor(tok), ip=_client_ip(request))
    return {
        "ok": True, "role": role, "persisted": persisted,
        "path": path if persisted else "",
        "note": ("口令已更新；其它会话持有的该角色令牌已作废（当前会话保留）。"
                 + ("已写入 .env，重启后依然有效" if persisted
                    else "★ .env 写入失败（%s），本次修改只在内存中生效，重启后会回到旧口令" % perr)),
    }


class ControlTtlIn(BaseModel):
    ttl_sec: int = Field(..., ge=0, le=2_592_000,
                         description="控制权限时长（秒）；0 = 不限时（直到后端重启 / 主动登出）")


@router.post("/control-ttl")
def api_control_ttl(body: ControlTtlIn, request: Request, tok: str = Depends(require_admin)):
    """设置控制权限时长（管理员）。0 = 不限时。

    ★ 只影响**之后新签发**的令牌：已签发的令牌保持签发时的有效期（不追溯 ——
      否则管理员刚调完时长，全厂在线会话会突然集体掉线）。
    同时写进根目录 .env（EFORT_CONTROL_TTL），重启后仍按这里的值启动。
    ★ 无论设多长，令牌都只在后端进程内存里 —— 重启后端后必须重新获取权限，
      这是刻意的：进程没了，授权凭据也不能留下。
    """
    sec = set_ttl(int(body.ttl_sec))
    try:
        path = _write_env_key("EFORT_CONTROL_TTL", str(sec))
        persisted = True
        perr = ""
    except OSError as e:
        path, persisted, perr = _env_path(), False, str(e)[:120]

    emit_event("auth", "info", "auth.ttl_changed",
               "控制权限时长已调整为 %s" % ("不限时" if sec == 0 else "%d 秒" % sec),
               {"ttl_sec": sec, "persisted": persisted, "path": path},
               actor=_actor(tok), ip=_client_ip(request))
    return {
        "ok": True, "ttl": sec, "persisted": persisted,
        "path": path if persisted else "",
        "note": ("只影响之后新签发的令牌；现有令牌保持签发时的有效期，重新登录即可按新时长签发。"
                 + ("已写入 .env，重启后端后按此值启动。" if persisted
                    else "★ .env 写入失败（%s），本次只在内存生效，重启后回到旧值" % perr)),
    }


@router.get("/summary")
def api_summary():
    """一眼看全：改了多少项、有哪些需要重启、真实下发是否被双重放行。"""
    desc = st.describe()
    flags = st.runtime_flags()
    restart_keys = [
        {"key": f["key"], "label": f["label"]}
        for g in desc["groups"] for f in g["fields"]
        if f["apply"] == "restart" and f["key"] in desc["overlay_keys"]
    ]
    return {
        "ok": True,
        "overridden": desc["overridden"],
        "overlay_keys": desc["overlay_keys"],
        "need_restart": restart_keys,
        "real_motion_active": flags["real_motion_active"],
        "real_motion_env": flags["real_motion_env"],
        "real_write": flags["real_write"],
        "service": {"name": SERVICE_NAME, "version": SERVICE_VERSION},
    }
