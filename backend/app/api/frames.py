# -*- coding: utf-8 -*-
"""
坐标系 API（阶段 6）：`config/frames.json` 的读写与标定。

GET  /api/frames          坐标系描述（★ 公开：设置页/只读页也要看得到）
PUT  /api/frames          整体替换（★ 管理员，改的是坐标系定义，等同改安全参数）
POST /api/frames/teach    由实测点位反推 → 写回（★ 管理员）
POST /api/frames/reset    恢复默认（★ 管理员）

为什么写接口要管理员：
  坐标系直接决定"按 X 键往哪走"。改错了不会报任何错，只会让机器人朝错误方向运动 ——
  与安全围栏同级，因此与围栏保存/重置用同一档权限（`require_admin`）。

`teach` 的定位：真机现场没有标定工装时，用"示教 + 读数"反推最常见：
  - tool：把末端对准一个已知位置，输入该点在**机器人坐标系**下的坐标 → 反推法兰→TCP 偏移；
  - user：直接给定工件原点在机器人坐标系下的位姿；
  - base：给出 X 轴在水平面内的偏航（航插对向按现场量）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_admin, token_role
from app.core import frames_config as fc
from app.core.config import get_config
from app.services import jog_frames as jf
from app.services.collector import collector
from app.services.events import emit as emit_event
from app.services.kinematics import fk_matrix, invert, tcp_of
from app.services.motion import motion

router = APIRouter(prefix="/api/frames", tags=["frames"])


def _actor(tok: str) -> str:
    return token_role(tok) or "unknown"


def _now_joints() -> List[float]:
    """当前关节角。

    ★ 标定要用**实际姿态**，所以优先取实时采集（真机读数），
      只有在没有实时数据时才退回"最近下发目标"（离线的可重复性用途）。
      反过来的话，真机上会把"命令目标"当成"实际到位姿态"，标出来的 TCP 是错的。
    """
    p = collector.get_latest()
    if p:
        try:
            return [float(p[f"j{i}"]) for i in range(1, 7)]
        except Exception:
            pass
    st = motion.state()
    if st.get("last_target"):
        return [float(v) for v in st["last_target"]]
    return [0.0] * 6


# ---------------------------------------------------------------- 读
@router.get("")
def api_get():
    """坐标系全量描述（公开）：四坐标系 + wobj 列表 + 轴单位表。"""
    cfg = fc.load_frames()
    frames = []
    for f in fc.describe()["frames"]:
        fid = f["id"]
        try:
            world = jf.world_pose(fid, cfg, f.get("user_id"))
        except Exception:
            world = None
        frames.append({**f, "world": world,
                       "unit_of": {str(i): jf.unit_of(fid, i) for i in range(1, 7)}})
    return {
        "ok": True, "frames": frames,
        "config": cfg, "path": fc.FRAMES_PATH,
        "ids": list(fc.FRAME_IDS), "user_ids": list(fc.USER_IDS),
        "current": {"joints": [round(v, 3) for v in _now_joints()],
                    "tcp": [round(v, 1) for v in tcp_of(_now_joints())]},
        "readonly": False,
    }


# ---------------------------------------------------------------- 写
class ToolIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=32)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0


class BaseIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=32)
    x_axis_yaw_deg: float = 0.0
    z_sign: int = 1


class WobjIn(BaseModel):
    id: str = Field(..., min_length=1, max_length=16)
    name: Optional[str] = Field(default=None, max_length=32)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0
    verified: bool = False


class FramesIn(BaseModel):
    base: Optional[BaseIn] = None
    tool: Optional[ToolIn] = None
    wobj: Optional[List[WobjIn]] = None


@router.put("")
def api_put(body: FramesIn, tok: str = Depends(require_admin)):
    """整体替换坐标系定义（未给的段沿用现值）。

    ★ 局部更新而不是全量覆盖：前端改一个"工具 Z 偏移"，不该把 base 的偏航顺手清零。
      合并规则是"段级替换、字段级默认取当前值"。
    """
    cur = fc.load_frames()
    merged: Dict[str, Any] = dict(cur)
    if body.base is not None:
        b = dict(cur.get("base") or {})
        b.update(body.base.model_dump(exclude_none=True))
        b["verified"] = float(b.get("x_axis_yaw_deg", 0.0)) != 0.0 or int(b.get("z_sign", 1) or 1) != 1
        merged["base"] = b
    if body.tool is not None:
        t = dict(cur.get("tool") or {})
        t.update(body.tool.model_dump(exclude_none=True))
        # TCP 偏移全零 = 未标定；非零即视为已标定（仅位置，姿态仍是保留字段）
        t["verified"] = any(abs(float(t.get(k) or 0.0)) > 1e-9 for k in ("x", "y", "z"))
        merged["tool"] = t
    if body.wobj is not None:
        merged["wobj"] = [w.model_dump() for w in body.wobj]

    try:
        saved = fc.save_frames(merged)
    except fc.FramesError as e:
        raise HTTPException(400, detail=str(e))
    except OSError as e:
        raise HTTPException(500, detail="坐标系写入失败：%s" % e)

    emit_event("config", "info", "config.frames_save", "坐标系配置已更新",
               {"base": saved["base"], "tool": saved["tool"],
                "wobj": [w["id"] for w in saved["wobj"]]}, actor=_actor(tok))
    return {"ok": True, "config": saved, "applied": "live"}


class TeachIn(BaseModel):
    target: str = Field(..., min_length=1, max_length=16)    # tool | user | base
    # tool：实测 TCP 在**机器人坐标系**下的位置(mm)
    # user：wobj 的 id + 位姿
    tcp: Optional[Dict[str, float]] = None
    user_id: Optional[str] = Field(default=None, max_length=16)
    pose: Optional[Dict[str, float]] = None
    yaw_deg: Optional[float] = None
    note: str = Field(default="", max_length=120)


@router.post("/teach")
def api_teach(body: TeachIn, tok: str = Depends(require_admin)):
    """由实测数据反推坐标系参数，并写回。

    - `target="tool"`：给 `tcp={x,y,z}`（实测 TCP 在机器人坐标系下的位置）+
      当前关节角 → 反推法兰→TCP 偏移。
    - `target="user"`：给 `user_id` + `pose={x,y,z,rx,ry,rz}`（工件原点在机器人坐标系下的位姿）。
    - `target="base"`：给 `yaw_deg`（X 轴在水平面内的偏航）。
    """
    tgt = str(body.target or "").strip().lower()
    cur = fc.load_frames()
    merged: Dict[str, Any] = dict(cur)
    detail: Dict[str, Any] = {"target": tgt}

    if tgt == "tool":
        if not body.tcp:
            raise HTTPException(400, detail="tool 标定需要 tcp={x,y,z}（实测 TCP 在机器人坐标系下的位置）")
        q = _now_joints()
        T_flange = fk_matrix(q)
        want = np.array([float(body.tcp.get("x", 0.0) or 0.0),
                         float(body.tcp.get("y", 0.0) or 0.0),
                         float(body.tcp.get("z", 0.0) or 0.0)], dtype=float)
        # 法兰→TCP 偏移：p_world = R_flange @ p_local + t_flange
        # ⇒ p_local = R_flangeᵀ @ (p_world − t_flange)
        #   只取旋转部分（invert 的 [:3,:3] 就是 R_flangeᵀ），不平移 —— 求的就是这个偏移本身。
        flange_pos = np.asarray(T_flange[:3, 3], dtype=float)
        local = invert(T_flange)[:3, :3] @ (want - flange_pos)
        t = dict(cur.get("tool") or {})
        t.update({"x": round(float(local[0]), 4), "y": round(float(local[1]), 4),
                  "z": round(float(local[2]), 4)})
        t["verified"] = True
        t["note"] = ("由示教反推：法兰 %s → TCP %s" % (
            [round(float(v), 1) for v in flange_pos], [round(float(v), 1) for v in want]))[:120]
        merged["tool"] = t
        detail["tool"] = {k: t[k] for k in ("x", "y", "z")}
        detail["joints"] = [round(v, 3) for v in q]
    elif tgt == "user":
        wid = str(body.user_id or "").strip()
        if not wid:
            raise HTTPException(400, detail="user 标定需要 user_id（如 wobj1）")
        if not body.pose:
            raise HTTPException(400, detail="user 标定需要 pose={x,y,z,rx,ry,rz}")
        ws = [dict(w) for w in (cur.get("wobj") or [])]
        hit = next((w for w in ws if w.get("id") == wid), None)
        if hit is None:
            if wid not in fc.USER_IDS:
                raise HTTPException(400, detail="用户坐标系 id 必须是 %s~%s" % (fc.USER_IDS[0], fc.USER_IDS[-1]))
            hit = {"id": wid, "name": wid}
            ws.append(hit)
        for k in ("x", "y", "z", "rx", "ry", "rz"):
            if body.pose.get(k) is not None:
                hit[k] = float(body.pose[k])
        hit["verified"] = True
        merged["wobj"] = ws
        detail["user_id"] = wid
        detail["pose"] = {k: hit.get(k) for k in ("x", "y", "z", "rx", "ry", "rz")}
    elif tgt == "base":
        if body.yaw_deg is None:
            raise HTTPException(400, detail="base 标定需要 yaw_deg（X 轴在水平面内的偏航角）")
        b = dict(cur.get("base") or {})
        b["x_axis_yaw_deg"] = float(body.yaw_deg)
        b["verified"] = True
        merged["base"] = b
        detail["yaw_deg"] = float(body.yaw_deg)
    else:
        raise HTTPException(400, detail="target 只能是 tool / user / base（收到 %s）" % body.target)

    try:
        saved = fc.save_frames(merged)
    except fc.FramesError as e:
        raise HTTPException(400, detail=str(e))
    emit_event("config", "info", "config.frames_teach",
               "坐标系标定已写入：%s" % tgt, {**detail, "note": body.note},
               actor=_actor(tok))
    return {"ok": True, "config": saved, "detail": detail, "applied": "live"}


@router.post("/reset")
def api_reset(tok: str = Depends(require_admin)):
    """恢复默认坐标系（base 偏航归零、tool 偏移归零、wobj 只留 wobj0）。"""
    saved = fc.reset_frames()
    emit_event("config", "warn", "config.frames_reset",
               "坐标系配置已恢复默认", {"path": fc.FRAMES_PATH}, actor=_actor(tok))
    return {"ok": True, "config": saved, "applied": "live"}


@router.get("/verify")
def api_verify():
    """标定体检：哪些项还没标定、会对点动造成什么影响。"""
    cfg = fc.load_frames()
    checks = []
    b = cfg["base"]
    checks.append({
        "key": "base_yaw", "ok": bool(b.get("verified")),
        "title": "机器人坐标系 X 方向" + ("已标定" if b.get("verified") else "未标定"),
        "value": b.get("x_axis_yaw_deg"),
        "hint": "" if b.get("verified")
                else "X 方向由底座航插对向决定，配置推不出来；未标定时按 X 键的绝对方向可能不对（相对增量仍正确）",
    })
    t = cfg["tool"]
    checks.append({
        "key": "tool", "ok": bool(t.get("verified")),
        "title": "工具 TCP" + ("已标定" if t.get("verified") else "未标定（按法兰中心处理）"),
        "value": {"x": t["x"], "y": t["y"], "z": t["z"]},
        "hint": "" if t.get("verified")
                else "未标定时以法兰中心作为 TCP：绕 A/B/C 旋转的支点会偏移实际 TCP 距离",
    })
    for w in cfg["wobj"]:
        if w.get("builtin"):
            continue
        checks.append({
            "key": "wobj:" + w["id"], "ok": bool(w.get("verified")),
            "title": "%s（%s）" % (w["id"], w["name"]),
            "value": {k: w[k] for k in ("x", "y", "z", "rx", "ry", "rz")},
            "hint": "" if w.get("verified") else "该用户坐标系未标定，方向仅供参考",
        })
    try:
        # ★ 走 get() 多级取值：Config 没有 .robot 属性，写 .robot.get(...) 会 AttributeError
        reach = float(get_config().get("robot", "reach_mm", default=712) or 712)
    except Exception:
        reach = 712.0
    return {"ok": True, "checks": checks, "reach_mm": reach,
            "all_verified": all(c["ok"] for c in checks)}
