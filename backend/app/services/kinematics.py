# -*- coding: utf-8 -*-
"""
ER8-700H 正运动学 (Forward Kinematics) + 模拟器。

DH 约定(标准): T_i = Rz(theta_i) * Tz(d_i) * Tx(a_i) * Rx(alpha_i)
                theta_i = q_i(关节角,度) + theta_offset_i

本模块只读 config/robot.yaml 的 dh 段, 换机型/改参数不碰逻辑。
坐标系: 右手系, 单位 mm。TCP 为末端法兰中心。
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

from app.core.config import get_config

# 模拟器安全关节范围 (deg) —— 仅用于离线演示, 不覆盖真实限位
_SIM_RANGE = [
    (-150, 150),   # J1
    (-90, 90),     # J2
    (-150, 150),   # J3
    (-180, 180),   # J4
    (-120, 120),   # J5
    (-360, 360),   # J6
]


def _dh_matrix(theta_deg: float, d: float, a: float, alpha_deg: float) -> np.ndarray:
    th = math.radians(theta_deg)
    al = math.radians(alpha_deg)
    ct, st = math.cos(th), math.sin(th)
    ca, sa = math.cos(al), math.sin(al)
    return np.array([
        [ct, -st * ca, st * sa, a * ct],
        [st, ct * ca, -ct * sa, a * st],
        [0.0, sa, ca, d],
        [0.0, 0.0, 0.0, 1.0],
    ], dtype=float)


def load_dh() -> List[dict]:
    """返回 6 个关节的 DH 参数列表, 含 d/a/alpha(deg)/theta_offset(deg)。"""
    cfg = get_config()
    joints = cfg.dh.get("joints", [])
    out = []
    for j in joints:
        out.append({
            "name": j.get("name", "?"),
            "d": float(j.get("d", 0.0)),
            "a": float(j.get("a", 0.0)),
            "alpha": float(j.get("alpha", 0.0)),
            "theta_offset": float(j.get("theta_offset", 0.0)),
        })
    return out


def forward_kinematics(joints_deg: List[float]) -> dict:
    """
    输入 6 个关节角(度), 返回:
      tcp: (x, y, z) mm
      frames: 7 个 4x4 矩阵 [base, after J1, ..., after J6] (用于可视化/校验)
    """
    dh = load_dh()
    n = len(dh)
    q = list(joints_deg) + [0.0] * max(0, n - len(joints_deg))
    frames: List[np.ndarray] = [np.eye(4)]
    T = np.eye(4)
    for i in range(n):
        theta = q[i] + dh[i]["theta_offset"]
        T = T @ _dh_matrix(theta, dh[i]["d"], dh[i]["a"], dh[i]["alpha"])
        frames.append(T)
    tcp = (float(T[0, 3]), float(T[1, 3]), float(T[2, 3]))
    return {"tcp": tcp, "frames": frames}


def tcp_of(joints_deg: List[float]) -> Tuple[float, float, float]:
    return forward_kinematics(joints_deg)["tcp"]


def simulate_pose(t: float) -> List[float]:
    """
    离线演示用的平滑合成轨迹 (deg)。t 为秒。
    各轴不同频率/相位, 整体在限位内缓慢摆动。
    """
    lo = [_r[0] for _r in _SIM_RANGE]
    hi = [_r[1] for _r in _SIM_RANGE]
    mid = [(a + b) / 2 for a, b in zip(lo, hi)]
    amp = [(b - a) / 2 * 0.6 for a, b in zip(lo, hi)]
    freq = [0.13, 0.17, 0.11, 0.19, 0.23, 0.07]
    phase = [0.0, 1.1, 2.3, 0.6, 1.7, 3.0]
    out = []
    for i in range(6):
        v = mid[i] + amp[i] * math.sin(2 * math.pi * freq[i] * t + phase[i])
        out.append(round(v, 3))
    return out


# ===================== 逆运动学 (数值法, 供指令预演) =====================

def fk_matrix(joints_deg: List[float]) -> np.ndarray:
    """末端法兰 4x4 齐次变换矩阵。"""
    dh = load_dh()
    q = list(joints_deg) + [0.0] * max(0, len(dh) - len(joints_deg))
    T = np.eye(4)
    for i in range(len(dh)):
        theta = q[i] + dh[i]["theta_offset"]
        T = T @ _dh_matrix(theta, dh[i]["d"], dh[i]["a"], dh[i]["alpha"])
    return T


def _rot_to_vec(R: np.ndarray) -> np.ndarray:
    """旋转矩阵 -> 旋转向量 (轴*角, rad)。"""
    cos_t = (np.trace(R) - 1.0) / 2.0
    cos_t = max(-1.0, min(1.0, cos_t))
    theta = math.acos(cos_t)
    if theta < 1e-9:
        return np.zeros(3)
    if abs(theta - math.pi) < 1e-6:      # 接近 180° 的退化情形
        A = (R + np.eye(3)) / 2.0
        k = np.sqrt(np.clip(np.diag(A), 0.0, None))
        if R[2, 1] - R[1, 2] < 0:
            k[0] = -k[0]
        if R[0, 2] - R[2, 0] < 0:
            k[1] = -k[1]
        if R[1, 0] - R[0, 1] < 0:
            k[2] = -k[2]
        n = np.linalg.norm(k)
        return (k / n) * theta if n > 1e-9 else np.zeros(3)
    k = np.array([R[2, 1] - R[1, 2],
                  R[0, 2] - R[2, 0],
                  R[1, 0] - R[0, 1]]) / (2.0 * math.sin(theta))
    return k * theta


def _pose_vec(T: np.ndarray) -> np.ndarray:
    """位姿 6 维向量: [x,y,z(mm), rx,ry,rz(rad)]。"""
    return np.concatenate([T[:3, 3], _rot_to_vec(T[:3, :3])])


def _numeric_jacobian(q_deg: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """数值差分雅可比 (6x6)。"""
    p0 = _pose_vec(fk_matrix(q_deg))
    J = np.zeros((6, 6))
    for i in range(6):
        q2 = q_deg.copy()
        q2[i] += eps
        J[:, i] = (_pose_vec(fk_matrix(q2)) - p0) / eps
    return J


def ikine(target_T: np.ndarray, q0: List[float],
          joint_limits: Optional[List[dict]] = None,
          max_iter: int = 300, tol_pos: float = 0.5,
          tol_rot: float = 0.01, rot_weight: float = 150.0) -> dict:
    """
    数值逆运动学 (阻尼最小二乘 DLS)。
    target_T:   目标 4x4 变换矩阵; q0: 初始关节角(度)
    rot_weight: 姿态权重(1rad≈N mm)。<=0 时**只约束位置**(不约束姿态)。
    返回 {ok, joints, iters, pos_err(mm), rot_err_deg}
    """
    q = np.array(list(q0) + [0.0] * max(0, 6 - len(q0)), dtype=float)[:6]
    pos_err = rot_err = float("inf")
    it = 0
    for it in range(max_iter):
        T = fk_matrix(q)
        dp = target_T[:3, 3] - T[:3, 3]
        pos_err = float(np.linalg.norm(dp))
        if rot_weight > 0:
            dr = _rot_to_vec(target_T[:3, :3] @ T[:3, :3].T)   # 世界系姿态误差
            rot_err = float(np.linalg.norm(dr))
        else:
            dr = np.zeros(3)
            rot_err = 0.0
        if pos_err < tol_pos and rot_err < tol_rot:
            break
        J6 = _numeric_jacobian(q)
        if rot_weight > 0:
            w = np.array([1.0, 1.0, 1.0, rot_weight, rot_weight, rot_weight])
            Jw = J6 * w[:, None]
            ew = np.concatenate([dp, dr]) * w
        else:
            Jw = J6[:3, :]        # 仅位置
            ew = dp
        lam = 0.2 if pos_err > 5 else 0.03    # 自适应阻尼
        A = Jw @ Jw.T + (lam ** 2) * np.eye(Jw.shape[0])
        try:
            dq = Jw.T @ np.linalg.solve(A, ew)
        except np.linalg.LinAlgError:
            break
        q = q + dq
        if joint_limits:
            for i, lim in enumerate(joint_limits[:6]):
                lo = float(lim.get("min", -360)); hi = float(lim.get("max", 360))
                q[i] = min(hi, max(lo, q[i]))   # 迭代中夹到限位
        if float(np.linalg.norm(dq)) < 1e-6:
            break
    return {
        "ok": bool(pos_err < tol_pos and rot_err < tol_rot),
        "joints": [round(float(v), 4) for v in q],
        "iters": it + 1,
        "pos_err": round(pos_err, 3),
        "rot_err_deg": round(math.degrees(rot_err), 3),
    }


def rpy_to_matrix(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    """ZYX 欧拉角(度) -> 3x3 旋转矩阵。"""
    rx, ry, rz = math.radians(rx_deg), math.radians(ry_deg), math.radians(rz_deg)
    Rx = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Ry = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    Rz = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx
