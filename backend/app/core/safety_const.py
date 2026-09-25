# -*- coding: utf-8 -*-
"""安全红线常量与轴锁策略：全项目唯一来源。

★ 为什么要有这个文件
  速度下限、轴锁、点动范围这三件事原先散落在 control.py / programs.py /
  motion.py / jog.py / vision_rules.py 五处，口径互相矛盾（有的 ge=1，有的
  default=100），是"改一处、漏四处"的根因。这里统一定义，各处 import。

★ 关于速度的口径（2026-09-25 用户明确）
  SPEED_MIN=5 是**工程边界**，不是"永远只能跑 5%"——操作员要跑多快由操作员判断。
  "AI 做真机验证时必须 5%" 属于操作纪律，不写死进代码。

★ 关于轴锁的口径（2026-09-25 用户明确）
  操作员在安全前提下 **J1–J6 全轴可控**；「仅 J6」不是系统默认限制，
  而是一个**可开关的模式**——默认关闭（全轴可动），只有 AI 需要真机验证时
  由人手动打开。开启方式：
      robot.yaml  motion.joint_lock.enabled: true   并重启
      或环境变量 EFORT_J6_ONLY=1
"""
from __future__ import annotations

import os
from typing import List

# ───────────────────────── 速度（工程边界） ─────────────────────────
SPEED_MIN = 5             # % 全局执行速度下限（含视觉自动执行）
SPEED_MAX = 100           # % 上限
JOG_SPEED_MIN = 5         # °/s 点动角速度下限
JOG_SPEED_MAX = 10        # °/s 点动角速度上限
TEACH_SPEED_MIN = 5       # % 示教滑块下限

# ───────────────── 轴锁（可配置模式，默认关闭） ─────────────────
DEFAULT_LOCKED_JOINTS = (1, 2, 3, 4, 5)   # 仅在模式开启时生效
DEFAULT_ONLY_JOINT = 6                    # 仅在模式开启时可动的轴
JOINT_LOCK_TOL_DEG = 0.5                  # 被锁轴允许的偏差容差（°）

# ───────────────────────── 时序常量 ─────────────────────────
TARGET_SETTLE_S = 0.15    # 写目标角后静置（40139~40144 回读前）
ESTOP_RETRY = 3           # 急停重发次数（急停不能只发一次）
JOG_TIMEOUT_S = 30        # 单次点动最长等待


def _cfg_get(*keys, default=None):
    """安全读取配置：任何异常都退回默认值，绝不让配置问题打断下发判据。"""
    try:
        from app.core.config import get_config
        return get_config().get(*keys, default=default)
    except Exception:
        return default


def joint_lock_enabled() -> bool:
    """轴锁模式是否开启。默认 False → J1~J6 全轴可控。

    操作员要全轴操控 ⇒ 保持默认 False。
    AI 需要真机验证 ⇒ 由人手动开启（改配置或 EFORT_J6_ONLY=1）并重启。
    """
    if os.getenv("EFORT_J6_ONLY", "") == "1":
        return True
    return bool(_cfg_get("motion", "joint_lock", "enabled", default=False))


def locked_joints() -> List[int]:
    """模式开启时被锁的轴列表。"""
    v = _cfg_get("motion", "joint_lock", "locked_joints",
                 default=list(DEFAULT_LOCKED_JOINTS))
    try:
        out = [int(x) for x in (v or [])]
    except Exception:
        out = list(DEFAULT_LOCKED_JOINTS)
    return [i for i in out if 1 <= i <= 6] or list(DEFAULT_LOCKED_JOINTS)


def only_joint() -> int:
    """模式开启时唯一可动的轴。"""
    try:
        v = int(_cfg_get("motion", "joint_lock", "only_joint",
                         default=DEFAULT_ONLY_JOINT))
    except Exception:
        v = DEFAULT_ONLY_JOINT
    return v if 1 <= v <= 6 else DEFAULT_ONLY_JOINT


def joint_lock_tol() -> float:
    """被锁轴允许的偏差容差（°）。"""
    try:
        return float(_cfg_get("motion", "joint_lock", "tolerance_deg",
                              default=JOINT_LOCK_TOL_DEG))
    except Exception:
        return JOINT_LOCK_TOL_DEG


def joint_lock_apply_in_sim() -> bool:
    """模拟/仿真链路是否同样受限。

    默认 True：避免"真机不能动、仿真能動"两套行为造成认知错位。
    """
    return bool(_cfg_get("motion", "joint_lock", "apply_in_sim", default=True))


def joint_lock_state() -> dict:
    """给 /api/system/health 与前端徽标用的状态快照。"""
    en = joint_lock_enabled()
    return {
        "enabled": en,
        "locked": locked_joints() if en else [],
        "only": only_joint() if en else None,
        "tolerance_deg": joint_lock_tol(),
        "apply_in_sim": joint_lock_apply_in_sim(),
    }


def clamp_speed(speed_pct) -> int:
    """把速度夹到工程边界内。None / 非法值 → SPEED_MIN（绝不静默归零或归百）。"""
    try:
        sp = int(speed_pct)
    except (TypeError, ValueError):
        return SPEED_MIN
    return max(SPEED_MIN, min(SPEED_MAX, sp))
