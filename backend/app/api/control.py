# -*- coding: utf-8 -*-
"""
指令预演 (L1, 零风险)。

输入目标(关节角 或 直角坐标) → 逆运动学求解 → 限位/可达校验 → 生成插值轨迹。
★ 本模块只做"计算 + 校验 + 预演轨迹", 不向机器人写入任何数据。
"""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import get_config
from app.services.collector import collector
from app.services.kinematics import fk_matrix, ikine, rpy_to_matrix, tcp_of

router = APIRouter(prefix="/api/control", tags=["control"])


# ---------- 工具 ----------
def _limits() -> List[dict]:
    lim = get_config().get("joint_limits", default=[])
    if lim:
        return lim
    return [{"name": f"J{i + 1}", "min": -180, "max": 180} for i in range(6)]


def _in_limits(q: List[float], limits: List[dict], tol: float = 1e-6) -> bool:
    for v, lim in zip(q, limits):
        if v < float(lim["min"]) - tol or v > float(lim["max"]) + tol:
            return False
    return True


def _start_joints(current: Optional[List[float]]) -> List[float]:
    if current and len(current) == 6:
        return [float(v) for v in current]
    p = collector.get_latest()
    if p:
        return [float(p[f"j{i}"]) for i in range(1, 7)]
    return [0.0] * 6


# ---------- Schemas ----------
class TcpTarget(BaseModel):
    x: float
    y: float
    z: float
    rx: Optional[float] = None   # 末端姿态(度, ZYX); 三者都缺省则沿用当前姿态
    ry: Optional[float] = None
    rz: Optional[float] = None


class PreviewIn(BaseModel):
    mode: str = "joint"                    # joint | cartesian
    joints: Optional[List[float]] = None   # 关节模式目标(度)
    tcp: Optional[TcpTarget] = None        # 直角模式目标(mm / deg)
    current: Optional[List[float]] = None  # 起点(缺省用当前实时姿态)
    duration_ms: int = Field(default=2500, ge=100, le=60000)
    steps: int = Field(default=60, ge=2, le=600)


# ---------- 端点 ----------
@router.get("/limits")
def api_limits():
    cfg = get_config()
    return {
        "joints": _limits(),
        "reach_mm": cfg.get("robot", "reach_mm", default=712),
        "readonly": True,   # 明确本接口只用于预演, 不下发
    }


@router.post("/preview")
def api_preview(body: PreviewIn):
    limits = _limits()
    q0 = _start_joints(body.current)

    result: dict = {
        "ok": False, "mode": body.mode,
        "start": [round(v, 3) for v in q0],
        "warnings": [], "violations": [], "solver": None,
        "readonly": True,
    }

    # ---- 求目标关节角 ----
    if body.mode == "joint":
        if not body.joints or len(body.joints) != 6:
            return {**result, "error": "关节模式需提供 6 个目标关节角"}
        q_target = [float(v) for v in body.joints]
        result["solver"] = {"type": "direct", "ok": True}
    elif body.mode == "cartesian":
        if body.tcp is None:
            return {**result, "error": "直角模式需提供 TCP 目标"}
        T_cur = fk_matrix(q0)
        target = np.eye(4)
        target[:3, 3] = [body.tcp.x, body.tcp.y, body.tcp.z]
        pose_given = (body.tcp.rx is not None and body.tcp.ry is not None
                      and body.tcp.rz is not None)
        if pose_given:
            target[:3, :3] = rpy_to_matrix(body.tcp.rx, body.tcp.ry, body.tcp.rz)
            rw = 150.0
        else:
            target[:3, :3] = T_cur[:3, :3]
            rw = 0.0     # 未指定姿态: 仅约束位置
            result["warnings"].append("未指定姿态, 仅约束末端位置")
        # 多初值 IK, 优先限位内 + 误差最小
        best = None
        for c0 in (q0, [0.0] * 6, [180.0, 0, 0, 0, 0, 0], [0, 0, 0, 180.0, 0, 0]):
            r = ikine(target, c0, limits, rot_weight=rw)
            ok_lim = _in_limits(r["joints"], limits)
            score = (0 if (r["ok"] and ok_lim) else 1, r["pos_err"])
            if best is None or score < best[0]:
                best = (score, r)
        r = best[1]
        q_target = r["joints"]
        result["solver"] = {
            "type": "ik", "ok": r["ok"], "iters": r["iters"],
            "pos_err_mm": r["pos_err"], "rot_err_deg": r["rot_err_deg"],
            "in_limits": _in_limits(q_target, limits),
        }
        if not r["ok"]:
            result["warnings"].append("IK 未完全收敛(位置误差 %.2f mm)" % r["pos_err"])
        if not _in_limits(q_target, limits):
            result["warnings"].append("IK 解超出关节限位(可尝试换目标点)")
    else:
        return {**result, "error": "未知模式: " + body.mode}

    # ---- 限位校验 ----
    violations = []
    for i, (v, lim) in enumerate(zip(q_target, limits)):
        lo, hi = float(lim["min"]), float(lim["max"])
        if v < lo or v > hi:
            violations.append({"joint": lim.get("name", f"J{i + 1}"),
                               "value": round(v, 2), "min": lo, "max": hi})
    result["violations"] = violations
    result["target"] = [round(v, 3) for v in q_target]

    # ---- 可达性 / 位移 ----
    tcp_start = tcp_of(q0)
    tcp_end = tcp_of(q_target)
    dist = math.dist(tcp_start, tcp_end)
    reach = float(get_config().get("robot", "reach_mm", default=712))
    result["tcp_start"] = [round(v, 1) for v in tcp_start]
    result["tcp_end"] = [round(v, 1) for v in tcp_end]
    result["distance_mm"] = round(dist, 1)
    if dist > reach * 2:
        result["warnings"].append("末端位移较大(%.0f mm), 请确认轨迹中间无干涉" % dist)

    # ---- 插值轨迹 (ease-in-out) ----
    steps = body.steps
    traj = []
    for k in range(steps + 1):
        f = k / steps
        s = 0.5 - 0.5 * math.cos(math.pi * f)
        frame = {"t": int(body.duration_ms * f)}
        for i in range(6):
            frame[f"j{i + 1}"] = round(q0[i] + (q_target[i] - q0[i]) * s, 3)
        traj.append(frame)
    result["trajectory"] = traj
    result["duration_ms"] = body.duration_ms
    result["ok"] = len(violations) == 0 and bool(result.get("solver", {}).get("ok"))
    return result


class IkIn(BaseModel):
    """轻量逆运动学：给定目标 TCP 位置，返回关节角（供"机器人模式"实时操控）。"""
    tcp: TcpTarget
    current: Optional[List[float]] = None  # 起点关节角(缺省用当前实时姿态)
    keep_orientation: bool = True          # True 保持当前姿态; False 仅约束位置


@router.post("/ik")
def api_ik(body: IkIn):
    """机器人模式操控：末端沿 X/Y/Z 平移，反解 6 关节角。只算不发，零风险。"""
    limits = _limits()
    q0 = _start_joints(body.current)
    T_cur = fk_matrix(q0)

    target = np.eye(4)
    target[:3, 3] = [body.tcp.x, body.tcp.y, body.tcp.z]
    pose_given = (body.tcp.rx is not None and body.tcp.ry is not None
                  and body.tcp.rz is not None)
    if pose_given:
        target[:3, :3] = rpy_to_matrix(body.tcp.rx, body.tcp.ry, body.tcp.rz)
        rw = 150.0
    elif body.keep_orientation:
        target[:3, :3] = T_cur[:3, :3]
        rw = 150.0
    else:
        rw = 0.0   # 仅约束位置，姿态自由（收敛更稳）

    # 多初值 IK，优先限位内 + 误差最小
    best = None
    for c0 in (q0, [0.0] * 6, [180.0, 0, 0, 0, 0, 0], [0, 0, 0, 180.0, 0, 0]):
        r = ikine(target, c0, limits, rot_weight=rw)
        ok_lim = _in_limits(r["joints"], limits)
        score = (0 if (r["ok"] and ok_lim) else 1, r["pos_err"])
        if best is None or score < best[0]:
            best = (score, r)
    r = best[1]
    q = r["joints"]

    violations = []
    for i, (v, lim) in enumerate(zip(q, limits)):
        lo, hi = float(lim["min"]), float(lim["max"])
        if v < lo or v > hi:
            violations.append({"joint": lim.get("name", f"J{i + 1}"),
                               "value": round(v, 2), "min": lo, "max": hi})

    return {
        "ok": r["ok"] and not violations,
        "joints": [round(v, 4) for v in q],
        "pos_err_mm": r["pos_err"],
        "rot_err_deg": r["rot_err_deg"],
        "in_limits": not violations,
        "violations": violations,
        "readonly": True,
    }
