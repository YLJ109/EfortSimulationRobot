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
    """数值差分雅可比 (6x6)。

    ★ 只作对照用（回归里拿它校验 jacobian_space 的位置行）。**不要**再拿它去做
      姿态求解 —— 它的姿态行是 d(rotvec)/dq，而 ikine 的姿态误差是世界系旋转向量，
      两者差一个左雅可比因子 J_l(v)⁻¹，用混的组合姿态永远收敛不到目标
      （症状：位置早就到位了，姿态误差卡在 1e-4 rad 量级再也不降）。
    """
    p0 = _pose_vec(fk_matrix(q_deg))
    J = np.zeros((6, 6))
    for i in range(6):
        q2 = q_deg.copy()
        q2[i] += eps
        J[:, i] = (_pose_vec(fk_matrix(q2)) - p0) / eps
    return J


def joint_axes(q_deg) -> List[Tuple[np.ndarray, np.ndarray]]:
    """各关节转轴在**世界系**里的 (轴方向 z_{i-1}, 轴上一点 p_{i-1})。

    DH 约定 T_i = Rz(θ_i)·Tz(d_i)·Tx(a_i)·Rx(α_i)：θ_i 是最内层的 Rz，
    所以它绕的是**第 i-1 个坐标系**的 z 轴，且轴过该坐标系原点。
    """
    dh = load_dh()
    n = len(dh)
    qq = list(q_deg)[:n] + [0.0] * max(0, n - len(q_deg))
    T = np.eye(4)
    out: List[Tuple[np.ndarray, np.ndarray]] = []
    for i in range(n):
        out.append((T[:3, 2].copy(), T[:3, 3].copy()))
        theta = qq[i] + dh[i]["theta_offset"]
        T = T @ _dh_matrix(theta, dh[i]["d"], dh[i]["a"], dh[i]["alpha"])
    return out


def jacobian_space(q_deg) -> np.ndarray:
    """6x6 **空间雅可比**（解析，世界系）。

    行 0~2 = 线速度 mm/deg（对 q 的度求导，故乘 π/180）；
    行 3~5 = 角速度 rad/deg（世界系角速度，同样乘 π/180）。

    ★ 为什么必须有它（而不是继续用数值差分）：
      姿态误差取的是 `rotvec(R_target · R_curᵀ)`，即"把当前姿态左乘多少能到目标"，
      与之配对的必须是**世界系角速度**雅可比 J_ω = z_{i-1}（再乘 π/180）。
      之前 ikine 用的是数值差分出的 d(rotvec(R))/dq，它等于 J_l(v)⁻¹·J_ω ——
      v（当前姿态旋转向量）较大时两者差别很大，姿态控制等于在用错的雅可比做迭代，
      结果就是"位置到位、姿态永远差一点"，最后靠 0.57° 的松容差蒙过去。
      解析式还顺带把每次迭代的 6 次 FK 省掉（数值差分要算 7 次）。
    """
    dh = load_dh()
    n = len(dh)
    qq = np.array(list(q_deg)[:n] + [0.0] * max(0, n - len(q_deg)), dtype=float)
    # 末端位置 = 全部关节连乘后的平移
    T = np.eye(4)
    for i in range(n):
        theta = qq[i] + dh[i]["theta_offset"]
        T = T @ _dh_matrix(theta, dh[i]["d"], dh[i]["a"], dh[i]["alpha"])
    p_e = T[:3, 3]

    k = math.pi / 180.0
    J = np.zeros((6, n))
    for i, (z, p) in enumerate(joint_axes(q_deg)):
        J[0:3, i] = np.cross(z, p_e - p) * k
        J[3:6, i] = z * k
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

    ★ 返回的是"迭代过程中误差最小的那个解"，不是最后一次迭代的结果。
      原因：不收敛时（目标不可达 / 接近奇异）DLS 会在附近来回走，最后一步往往不是
      最好的一步；把最后一步返回出去，调用方拿到的可能比初值还差。
    """
    q = np.array(list(q0) + [0.0] * max(0, 6 - len(q0)), dtype=float)[:6]
    pos_err = rot_err = float("inf")
    best_q = q.copy()
    best_pos_err, best_rot_err = float("inf"), float("inf")
    best_cost = float("inf")
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
        # 代价函数与 DLS 实际最小化的量一致（位置 mm 与 加权姿态 同量纲相加）
        cost = pos_err ** 2 + (rot_weight * rot_err) ** 2
        if cost < best_cost:
            best_cost = cost
            best_q = q.copy()
            best_pos_err, best_rot_err = pos_err, rot_err
        if pos_err < tol_pos and rot_err < tol_rot:
            break
        # ★ 解析空间雅可比：姿态行是世界系角速度，与上面的 dr 严格配对
        J6 = jacobian_space(q)
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
                lo = float(lim.get("min", -360))
                hi = float(lim.get("max", 360))
                q[i] = min(hi, max(lo, q[i]))   # 迭代中夹到限位
        if float(np.linalg.norm(dq)) < 1e-9:
            break
    return {
        "ok": bool(best_pos_err < tol_pos and best_rot_err < tol_rot),
        "joints": [round(float(v), 4) for v in best_q],
        "iters": it + 1,
        "pos_err": round(best_pos_err, 3),
        "rot_err_deg": round(math.degrees(best_rot_err), 3),
    }


def rpy_to_matrix(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    """ZYX 欧拉角(度) -> 3x3 旋转矩阵。"""
    rx, ry, rz = math.radians(rx_deg), math.radians(ry_deg), math.radians(rz_deg)
    Rx = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Ry = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    Rz = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def matrix_to_rpy(R: np.ndarray) -> Tuple[float, float, float]:
    """3x3 旋转矩阵 -> ZYX 欧拉角(度)，与 rpy_to_matrix 互逆。

    ★ 必须有万向锁分支：ry = ±90° 时 cos(ry) = 0，rx 与 rz 退化成同一个自由度，
      直接用 atan2 相除会得到 0/0。此处锁死 rz = 0，把全部偏航交给 rx ——
      反解结果不唯一，但**与 rpy_to_matrix 复合回去是同一个矩阵**，这就够了
      （界面只要求"能显示、能改回去"，不要求角度值唯一）。
    """
    sy = float(-R[2, 0])
    sy = max(-1.0, min(1.0, sy))
    ry = math.asin(sy)
    if abs(sy) > 1.0 - 1e-9:
        rx = math.atan2(-float(R[1, 2]), float(R[1, 1]))
        rz = 0.0
    else:
        rx = math.atan2(float(R[2, 1]), float(R[2, 2]))
        rz = math.atan2(float(R[1, 0]), float(R[0, 0]))
    return (math.degrees(rx), math.degrees(ry), math.degrees(rz))


def unit_axis(i: int) -> np.ndarray:
    """单位轴向量 e_i（i = 1/2/3 → X/Y/Z）。"""
    v = np.zeros(3)
    v[int(i) - 1] = 1.0
    return v


def rot_about_axis(axis_vec, angle_rad: float) -> np.ndarray:
    """绕**任意单位轴**转 angle_rad 的 3x3 旋转矩阵（Rodrigues 公式）。

    ★ 为什么需要"任意轴"版本：直角系点动里，A/B/C 的旋转轴是**坐标系轴在工具系里的方向**，
      它一般不是 e_x/e_y/e_z（比如 J1 转了 90° 之后，机器人系的 B 轴在工具系里指向工具 X）。
      只支持绕坐标轴旋转的写法，会在 J1 非零时把 B 按钮转成完全错误的方向。
    """
    a = np.asarray(axis_vec, dtype=float).reshape(3)
    n = float(np.linalg.norm(a))
    if n < 1e-12:
        return np.eye(3)
    k = a / n
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    K = np.array([
        [0.0, -k[2], k[1]],
        [k[2], 0.0, -k[0]],
        [-k[1], k[0], 0.0],
    ])
    return np.eye(3) * c + s * K + (1.0 - c) * np.outer(k, k)


def pose_of(joints_deg: List[float]) -> dict:
    """末端位姿(x,y,z mm + rx,ry,rz deg) —— 直角坐标系点动/标定的统一读数。"""
    T = fk_matrix(joints_deg)
    rx, ry, rz = matrix_to_rpy(T[:3, :3])
    return {
        "x": round(float(T[0, 3]), 3),
        "y": round(float(T[1, 3]), 3),
        "z": round(float(T[2, 3]), 3),
        "rx": round(rx, 4),
        "ry": round(ry, 4),
        "rz": round(rz, 4),
    }


def translate_matrix(dx: float, dy: float, dz: float) -> np.ndarray:
    """平移变换（mm）。"""
    T = np.eye(4)
    T[0, 3] = float(dx)
    T[1, 3] = float(dy)
    T[2, 3] = float(dz)
    return T


def frame_matrix(pose: Optional[dict]) -> np.ndarray:
    """{x,y,z,rx,ry,rz} -> 4x4 变换。缺省的键按 0 处理。"""
    p = pose or {}
    T = np.eye(4)
    T[:3, :3] = rpy_to_matrix(float(p.get("rx", 0.0) or 0.0),
                              float(p.get("ry", 0.0) or 0.0),
                              float(p.get("rz", 0.0) or 0.0))
    T[0, 3] = float(p.get("x", 0.0) or 0.0)
    T[1, 3] = float(p.get("y", 0.0) or 0.0)
    T[2, 3] = float(p.get("z", 0.0) or 0.0)
    return T


def invert(T: np.ndarray) -> np.ndarray:
    """4x4 齐次变换求逆（解析法，比 np.linalg.inv 稳且不会因接近奇异而报错）。"""
    R = T[:3, :3]
    t = T[:3, 3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ t
    return out
