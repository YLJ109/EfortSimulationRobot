# -*- coding: utf-8 -*-
"""
直角坐标系（机器人 / 工具 / 用户）点动求解器。

## 为什么单独一个模块

`core/frames_config.py` 只管"配置长什么样"（纯 JSON，不碰 numpy）；
"坐标系怎么进到求解里"是解析几何，属于 services。分开之后：
  - 配置层可以在没有 numpy 的环境里单独测；
  - 求解层是**纯函数**（给定关节角就有确定输出），可以在 node/pytest 里直接钉死方向。

## 四条几何约定（写反了方向就反，而且不容易发现）

1. **相对点动只用坐标系的"姿态"，不用它的"原点"**。
   点动是相对运动：沿机器人系 X 走 1mm，只是"往基座 X 方向挪 1mm"，跟基座原点在哪无关。
   原点只在**绝对定位/显示**（"把 TCP 移到 wobj1 的 X=100"）时才有意义。
   ★ 这一条决定了下面第 3 条为什么是对的。

2. **平移是"坐标系的轴在世界里的方向"**：`T_goal.position = T_cur.position + W_R · e_k · d`。

3. **旋转绕"当前 TCP"转，不绕世界原点转**（这是示教器的实际行为）。
   绕世界原点转会让 TCP 画一个大圆弧 —— 现场描述是"按了 A 键，机器人跑到别处去了"。
   而且旋转轴要取**坐标系轴在工具系里的方向**：
   `a_local = (R_cur · R_ft)ᵀ · W_R · e_k`，再对工具姿态右乘 `Rot(a_local, θ)`。
   只支持"绕 e_x/e_y/e_z"的写法会在 J1 非零时把 B 键转成完全错误的方向。

4. **`base` / `user` / `tool` 共用一条路径**：差别只在 `W_R` 取谁 ——
   `base` 取基座系姿态、`user` 取"基座系 ∘ wobj 自身位姿"、`tool` 取末端姿态。
   因为 `wobj0 ≡ 机器人坐标系`（官方明文），所以 base 与 wobj0 天然一致，不用写两份。

## 单位按轴，不按坐标系
直角系里 1/2/3 是**平移（mm）**、4/5/6 是**旋转（deg）**（4±=A 绕 Z、5±=B 绕 Y、6±=C 绕 X）；
关节系恒为度。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.core.frames_config import CART_AXES, JOINT_AXES, get_wobj, load_frames
from app.services import kinematics as kin

# 直角系各轴的最大"轴速度"（轴 1~3 单位 mm/s，轴 4~6 单位 deg/s）。
# 默认值按 T1 教学档：线速度 50 mm/s（官方 T1 上限），姿态 10 °/s。
DEFAULT_MAX_MMPS = 50.0
DEFAULT_MAX_ROT_DPS = 10.0

# ★★ IK 容差必须**远小于最小步长**，否则小步长会被判成"已经到位"而原地不动：
#    ikine() 默认 tol_pos=0.5mm / tol_rot=0.01rad(0.57°)，而最小步长是 0.1mm / 0.1°
#    → 一进循环就满足收敛条件，直接返回原关节角，症状是"0.1 档按了没反应"。
#    这里取 0.01mm / 1e-4rad(0.006°)，都在最小步长的 1/10 以下。
IK_TOL_POS = 0.01
IK_TOL_ROT = 1e-4
IK_MAX_ITER = 80


# ---------------------------------------------------------------------------
# 坐标系 → 姿态矩阵
# ---------------------------------------------------------------------------
def base_matrix(cfg: Optional[Dict[str, Any]] = None) -> np.ndarray:
    """机器人坐标系相对 DH 基座系的变换。

    ★ `x_axis_yaw_deg` 无法从配置推导（取决于底座航插朝向），默认 0 且 `verified=false`。
      未标定时**不禁止点动**（否则真机联调前完全没法用），只在前端标注"方向未标定"。
    """
    cfg = cfg or load_frames()
    b = cfg.get("base") or {}
    yaw = math.radians(float(b.get("x_axis_yaw_deg", 0.0) or 0.0))
    R = np.array([
        [math.cos(yaw), -math.sin(yaw), 0.0],
        [math.sin(yaw), math.cos(yaw), 0.0],
        [0.0, 0.0, 1.0],
    ])
    if int(b.get("z_sign", 1) or 1) < 0:
        # Z 反向 ⇒ 绕 X 翻 180°。★ 用旋转（det=+1）而不是 diag(1,1,-1)：
        # 后者是镜面（det=-1），会把右手系变左手系，之后所有叉乘/旋转方向全反。
        R = R @ np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]])
    T = np.eye(4)
    T[:3, :3] = R
    return T


def tool_matrix(cfg: Optional[Dict[str, Any]] = None) -> np.ndarray:
    """法兰 → TCP 的变换。

    ★ 本项目 TCP 只有位置没有姿态（`tool.rpy` 是保留字段、**不参与求解**），
      所以工具系姿态 == 法兰姿态。等真机标完姿态再打开这里的 rpy。
    """
    cfg = cfg or load_frames()
    t = cfg.get("tool") or {}
    T = np.eye(4)
    T[0, 3] = float(t.get("x", 0.0) or 0.0)
    T[1, 3] = float(t.get("y", 0.0) or 0.0)
    T[2, 3] = float(t.get("z", 0.0) or 0.0)
    return T


def user_matrix(cfg: Optional[Dict[str, Any]] = None,
                user_id: Optional[str] = None) -> np.ndarray:
    """机器人坐标系 → 用户坐标系。wobj0 为恒等（即机器人坐标系本身）。"""
    cfg = cfg or load_frames()
    w = get_wobj(cfg, user_id or "wobj0")
    if w is None:
        raise ValueError("用户坐标系不存在：" + str(user_id))
    return kin.frame_matrix({k: w.get(k, 0.0) for k in ("x", "y", "z", "rx", "ry", "rz")})


def frame_rotation(frame: str, r_tool_world: np.ndarray,
                   cfg: Optional[Dict[str, Any]] = None,
                   user_id: Optional[str] = None) -> np.ndarray:
    """坐标系在**世界系**里的姿态（3x3）。关节系没有统一点，调用方不该问它要。"""
    if frame == "tool":
        return np.asarray(r_tool_world, dtype=float)
    B = base_matrix(cfg)
    if frame == "base":
        return B[:3, :3]
    return (B @ user_matrix(cfg, user_id))[:3, :3]


def world_pose(frame: str, cfg: Optional[Dict[str, Any]] = None,
               user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """坐标系在 DH 世界系里的位姿（给界面显示用）。joint / tool 没有固定世界位姿。"""
    if frame in ("joint", "tool"):
        return None
    T = base_matrix(cfg) if frame == "base" else (base_matrix(cfg) @ user_matrix(cfg, user_id))
    rx, ry, rz = kin.matrix_to_rpy(T[:3, :3])
    return {"x": round(float(T[0, 3]), 3), "y": round(float(T[1, 3]), 3),
            "z": round(float(T[2, 3]), 3),
            "rx": round(rx, 4), "ry": round(ry, 4), "rz": round(rz, 4)}


def world_matrix(frame: str, cfg: Optional[Dict[str, Any]] = None,
                 user_id: Optional[str] = None) -> np.ndarray:
    """坐标系在 DH 世界系里的 4x4 齐次变换矩阵。joint / tool 返回恒等矩阵。"""
    if frame in ("joint", "tool"):
        return np.eye(4)
    return base_matrix(cfg) if frame == "base" else (base_matrix(cfg) @ user_matrix(cfg, user_id))


# ---------------------------------------------------------------------------
# 求解一个增量
# ---------------------------------------------------------------------------
def solve_delta(q_cur: List[float], frame: str, axis: int, amount: float,
                user_id: Optional[str] = None,
                limits: Optional[List[dict]] = None,
                cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """在指定坐标系里走一个轴增量 → 目标关节角。纯计算，不下发。

    q_cur:  当前关节角(度)，也是 IK 的**唯一初值**
    amount: 1/2/3 为 mm、4/5/6 为 deg，带符号
    返回 {ok, joints, pos_err_mm, rot_err_deg, iters, tcp_before, tcp_after}
    """
    cfg = cfg or load_frames()
    if frame == "joint":
        raise ValueError("关节系不走直角求解器，请直接对单轴加减")
    a = int(axis)
    if a < 1 or a > 6:
        raise ValueError("轴号只能是 1~6（收到 %s）" % axis)

    q0 = [float(v) for v in q_cur][:6]
    T_flange = kin.fk_matrix(q0)
    T_ft = tool_matrix(cfg)
    T_tcp = T_flange @ T_ft
    R_tool_w = T_tcp[:3, :3]
    W_R = frame_rotation(frame, R_tool_w, cfg, user_id)

    T_goal_tcp = np.eye(4)
    T_goal_tcp[:3, 3] = T_tcp[:3, 3]
    if a <= 3:
        # ---- 平移：沿"坐标系轴在世界里的方向"挪，原点不参与 ----
        T_goal_tcp[:3, :3] = T_tcp[:3, :3]
        T_goal_tcp[:3, 3] = T_tcp[:3, 3] + W_R @ kin.unit_axis(a) * float(amount)
    else:
        # ---- 旋转：绕**当前 TCP**转，轴向取坐标系轴在工具系里的方向 ----
        # 4±=A 绕 Z、5±=B 绕 Y、6±=C 绕 X（官方定义，写反了"B 键会转错侧"）
        k = {4: 3, 5: 2, 6: 1}[a]
        axis_local = R_tool_w.T @ (W_R @ kin.unit_axis(k))
        R_d = kin.rot_about_axis(axis_local, math.radians(float(amount)))
        T_goal_tcp[:3, :3] = R_tool_w @ R_d

    T_flange_goal = T_goal_tcp @ kin.invert(T_ft)

    # ★ IK 初值必须是**当前关节角**，且只解一次：
    #   多初值（control.py 的 _ik_best）挑的是"误差最小"的解，而点动要的是**构型连续**。
    #   用多初值的症状是：点着点着机器人突然甩到另一个姿态（翻肩/翻肘），人完全无法预判。
    r = kin.ikine(T_flange_goal, q0, limits or [], rot_weight=150.0,
                  max_iter=IK_MAX_ITER, tol_pos=IK_TOL_POS, tol_rot=IK_TOL_ROT)
    return {
        "ok": bool(r["ok"]),
        "joints": [float(v) for v in r["joints"]],
        "pos_err_mm": float(r["pos_err"]),
        "rot_err_deg": float(r["rot_err_deg"]),
        "iters": int(r["iters"]),
        "tcp_before": kin.pose_of(q0),
        "tcp_after": kin.pose_of(r["joints"]),
    }


# ---------------------------------------------------------------------------
# 限位 / 速度辅助
# ---------------------------------------------------------------------------
def _limit_hits(q: List[float], limits: List[dict], tol: float = 1e-6,
                q_ref: Optional[List[float]] = None) -> List[dict]:
    """返回"顶到限位"的关节。

    ★ 给了 q_ref 就只报**这一拍真的动了、并且动到限位**的关节：
      否则"某个轴本来就停在限位上、这拍在动别的轴"会被误判成撞限位而直接停住 ——
      症状是"点 J2 一点就停，提示 J4 到限位"。
    """
    out: List[dict] = []
    for i, v in enumerate(list(q)[:6]):
        if i >= len(limits or []):
            break
        if q_ref is not None:
            ref = list(q_ref)[i] if i < len(q_ref) else 0.0
            if abs(float(v) - float(ref)) <= 1e-9:
                continue
        lo = float(limits[i].get("min", -360.0))
        hi = float(limits[i].get("max", 360.0))
        if float(v) <= lo + tol or float(v) >= hi - tol:
            out.append({"joint": limits[i].get("name", "J%d" % (i + 1)),
                        "value": round(float(v), 3), "min": lo, "max": hi})
    return out


def _max_abs_dq(q_a: List[float], q_b: List[float]) -> float:
    return max(abs(float(a) - float(b)) for a, b in zip(list(q_a)[:6], list(q_b)[:6]))


def plan_step(q_cur: List[float], frame: str, axis: int, direction: int,
              amount: float, speed: float, user_id: Optional[str] = None,
              limits: Optional[List[dict]] = None, max_joint_dps: float = 30.0,
              cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """直角系**增量点动**（按一次走固定距离/角度）。

    与连续点动的区别：增量是"用户点名要走这么多"，所以**不做步长缩减** ——
    解不出来就如实失败并给奇异提示，而不是悄悄少走一点让人以为走对了。
    只有在**关节限位**上才夹紧并回报（与关节系的行为保持一致）。
    """
    cfg = cfg or load_frames()
    limits = limits or []
    d = 1 if int(direction) >= 0 else -1
    req = abs(float(amount))
    r = solve_delta(q_cur, frame, axis, d * req, user_id, limits, cfg)
    out: Dict[str, Any] = {
        "ok": False, "frame": frame, "axis": int(axis), "dir": d,
        "requested": round(req, 4),
        "tcp_before": r.get("tcp_before"), "tcp_after": r.get("tcp_after"),
    }
    if not r["ok"]:
        out["reason"] = "该方向求解失败"
        out["singular"] = singular_hint(q_cur)
        out["pos_err_mm"] = round(float(r["pos_err_mm"]), 4)
        return out

    q = [float(v) for v in r["joints"]]
    clamped = False
    for i, lim in enumerate(limits[:6]):
        lo, hi = float(lim.get("min", -360.0)), float(lim.get("max", 360.0))
        if q[i] < lo:
            q[i], clamped = lo, True
        elif q[i] > hi:
            q[i], clamped = hi, True

    sp = max(1e-3, abs(float(speed)))
    duration_s = req / sp
    need = _max_abs_dq(q, q_cur) / max(duration_s, 1e-3)
    out.update({
        "ok": True, "joints": q, "clamped": clamped,
        "need_dps": round(need, 3),
        "speed_scale": round(min(1.0, max_joint_dps / need), 4) if need > 1e-9 else 1.0,
        "duration_ms": int(round(duration_s * 1000)),
        "hit_limit": _limit_hits(q, limits) if clamped else [],
        "pos_err_mm": round(float(r["pos_err_mm"]), 4),
        "rot_err_deg": round(float(r["rot_err_deg"]), 4),
    })
    return out


MAX_SHRINK_TRIES = 6
# 步长缩到"请求值"的这个比例以下就放弃：再小下去只是在原地抖，没有意义
MIN_TICK_FRACTION = 1e-4


def plan_tick(q_ref: List[float], frame: str, axis: int, direction: int,
              speed: float, tick_s: float, user_id: Optional[str] = None,
              limits: Optional[List[dict]] = None, max_joint_dps: float = 30.0,
              cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """直角系**连续点动**一个 tick 的求解。

    ★ 为什么必须"缩步长"而不是"把 pct 封到 100"：
      pct 封顶只能限制**伺服上限**，但下一个 tick 的目标是从**上一个目标**积分出来的 ——
      目标会一路跑到伺服追不上的位置，松手后机器人还在走（"目标跑在伺服前面"）。
      正确做法是保证**每 tick 的目标位移都在一个周期内可完成**：超了就缩步长，
      缩了多少通过 `scale` 如实回报（前端显示"已限速 40%"）。
    """
    cfg = cfg or load_frames()
    limits = limits or []
    d = 1 if int(direction) >= 0 else -1
    tick_s = max(1e-3, float(tick_s))
    requested = abs(float(speed)) * tick_s          # 轴单位（mm 或 deg）
    scale = 1.0
    last: Dict[str, Any] = {}

    for _ in range(MAX_SHRINK_TRIES):
        if requested * scale <= requested * MIN_TICK_FRACTION:
            break
        r = solve_delta(q_ref, frame, axis, d * requested * scale, user_id, limits, cfg)
        if not r["ok"]:
            last = r
            scale *= 0.5                            # 不收敛（多半接近奇异）→ 减半步长
            continue
        q = [float(v) for v in r["joints"]]
        need = _max_abs_dq(q, q_ref) / tick_s
        if need <= max_joint_dps * (1 + 1e-9):
            return {
                "ok": True, "joints": q, "scale": round(scale, 4),
                "need_dps": round(need, 3), "delta": round(requested * scale, 4),
                "tcp_before": r.get("tcp_before"), "tcp_after": r.get("tcp_after"),
                "pos_err_mm": round(float(r["pos_err_mm"]), 4),
                "rot_err_deg": round(float(r["rot_err_deg"]), 4),
                "hit_limit": _limit_hits(q, limits, q_ref=q_ref),
            }
        # 恰好缩到"不超过 max_joint_dps"；乘 0.999 留一点浮点余量，避免临界反复
        scale *= (max_joint_dps / need) * 0.999
        last = r

    return {
        "ok": False, "scale": round(scale, 6),
        "reason": ("目标位移无法在一个下发周期内完成" if last.get("ok") else "该方向求解失败"),
        "singular": singular_hint(q_ref),
        "pos_err_mm": round(float((last or {}).get("pos_err_mm") or 0.0), 4),
    }


# ---------------------------------------------------------------------------
# 奇异点提示
# ---------------------------------------------------------------------------
def singular_hint(q: List[float]) -> str:
    """按关节角给一句"哪里接近奇异"的提示（EFORT 三类奇异的判别很直接）。

    三类奇异（官方手册）：
      腕部  J5 ≈ 0°        → J4 与 J6 轴共线，绕工具 Z 失去自由度
      肩部  TCP 接近 J1 轴  → J1 的方向失去意义
      肘部  手臂接近伸直    → J2/J3 共线

    处置**都是同一个**：切回关节坐标系，用单轴微动挪开。
    """
    try:
        q5 = abs(float(list(q)[4]))
        if q5 < 6.0:
            return "手腕奇异（J5≈0）：绕工具 Z 的方向已失去自由度"
        reach = 712.0
        try:
            # ★ Config 没有 .robot 属性（只有 raw/get/dh/vision 等）：
            #   写成 get_config().robot.get(...) 会抛 AttributeError，被下面的 except 吞掉，
            #   结果是"配置里改了 reach_mm 却永远按 712 算"。必须走 get() 多级取值。
            from app.core.config import get_config
            reach = float(get_config().get("robot", "reach_mm", default=712) or 712)
        except Exception:
            pass
        tcp = kin.tcp_of(q)
        r_xy = math.hypot(float(tcp[0]), float(tcp[1]))
        r_all = math.sqrt(r_xy ** 2 + float(tcp[2]) ** 2)
        if r_xy < 30.0:
            return "肩部奇异：末端接近 J1 轴线，J1 方向失去意义"
        if reach > 0 and r_all > reach * 0.97:
            return "肘部奇异：手臂接近完全伸直"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# 描述辅助
# ---------------------------------------------------------------------------
def axes_of(frame: str) -> List[str]:
    return list(JOINT_AXES) if frame == "joint" else list(CART_AXES)


def unit_of(frame: str, axis: int) -> str:
    if frame == "joint":
        return "deg"
    return "mm" if int(axis) <= 3 else "deg"


def is_cartesian(frame: str) -> bool:
    return frame in ("base", "tool", "user")


def norm_frame(frame: Optional[str]) -> Tuple[str, str]:
    """归一化坐标系入参 → (frame, 错误信息)。缺省/空 = joint（**向后兼容**）。"""
    f = str(frame or "").strip().lower() or "joint"
    if f not in ("joint", "base", "tool", "user"):
        return "joint", "坐标系只能是 joint / base / tool / user（收到 %s）" % (frame,)
    return f, ""
