# -*- coding: utf-8 -*-
"""
围栏实时状态守卫（阶段 4 安全加固）。

背景：安全围栏的余量是在**前端 3D 场景**里逐帧算出来的（three/safety.js），后端原本
看不见这个状态，导致"/api/control/move"的下发拦截只能靠前端自觉 —— 一旦换成脚本/第三方
客户端调用接口，围栏互锁就形同虚设。

做法：前端每 ~1s 把当前围栏状态上报给后端（POST /api/safety/live），后端记住时间戳；
执行引擎在下发运动前调用 check()：
  - 无记录                  → 放行，但写一条 warn 事件（互锁未生效，提示前端未运行）
                                ★ 若配置 motion.require_live_safety=true，则改为拒绝
  - 记录已过期(>STALE_SEC)   → 视为失效，同上处理
  - state = danger / hit     → 直接拒绝（这是真正的安全互锁）

失败策略默认为"放行 + 告警"，是为了不影响只用 API 的自动化场景；追求更严格可在
config/robot.yaml 里打开 motion.require_live_safety。
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from app.core.config import get_config

STALE_SEC = 5.0           # 超过这个时间没有上报，认为状态失效
UNSAFE = ("danger", "hit")

_latest: Optional[Dict[str, Any]] = None
_lock = Lock()


def update(state: str, zone_id: str = "", zone_name: str = "",
           clearance: float = 0.0, ratio: float = 1.0) -> Dict[str, Any]:
    """上报当前围栏状态（前端每帧评估后节流调用）。"""
    global _latest
    # ★ 审计修复 P3：clearance/ratio 可能是 NaN —— `float('nan') or 0.0` 是
    #   truthy，`or` 拦不住，NaN 会一路进快照再进 JSON（前端 JSON.parse 抛错）。
    try:
        c = float(clearance)
    except (TypeError, ValueError):
        c = 0.0
    if c != c or c in (float("inf"), float("-inf")):
        c = 0.0
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        r = 1.0
    if r != r or r in (float("inf"), float("-inf")):
        r = 1.0
    rec = {
        "state": str(state or "safe"),
        "zone_id": str(zone_id or ""),
        "zone_name": str(zone_name or ""),
        "clearance": c,
        "ratio": r,
        "ts": time.time(),
    }
    with _lock:
        _latest = rec
    return snapshot()


def snapshot() -> Dict[str, Any]:
    """返回当前快照 + 是否新鲜（供 API/前端诊断互锁是否生效）。"""
    with _lock:
        rec = _latest
    if not rec:
        return {"reported": False, "fresh": False, "state": "unknown", "age_sec": None}
    age = time.time() - rec["ts"]
    return {
        "reported": True,
        "fresh": age <= STALE_SEC,
        "age_sec": round(age, 2),
        "state": rec["state"],
        "zone_id": rec["zone_id"],
        "zone_name": rec["zone_name"],
        "clearance": rec["clearance"],
        "ratio": rec["ratio"],
        "interlocked": _interlocked_from_config(),
    }


def _interlocked_from_config() -> bool:
    """是否强制要求"必须有实时围栏状态"才允许下发。"""
    try:
        return bool(get_config().get("motion", "require_live_safety", default=False))
    except Exception:
        return False


def check() -> Tuple[bool, str, Dict[str, Any]]:
    """下发前的安全校验。@return (是否允许, 原因, 快照)"""
    snap = snapshot()
    if not snap["reported"]:
        if snap.get("interlocked"):
            return False, "围栏互锁已启用，但尚未收到前端实时状态，拒绝下发", snap
        return True, "未收到围栏实时状态（互锁未生效）", snap

    if not snap["fresh"]:
        # ★ 审计修复 P2：过期时必须**保留最后状态的方向性**。
        #   原实现"过期就放行"——浏览器崩溃前最后一帧是 danger，5s 后照常放行，
        #   等于给了一条"只要让前端掉线就能解除互锁"的捷径。
        #   现在：最后一帧是 danger/hit 且已过期 → 一律拒绝（fail-safe）；
        #   恢复方式是前端恢复上报（安全态会覆盖掉），或操作员复位。
        if snap.get("state") in UNSAFE:
            return False, (f"围栏状态已过期({snap['age_sec']}s)，"
                           f"且过期前最后状态为 {snap['state']}，拒绝下发"), snap
        if snap.get("interlocked"):
            return False, f"围栏状态已过期({snap['age_sec']}s)，拒绝下发", snap
        return True, f"围栏状态已过期({snap['age_sec']}s)，互锁未生效", snap

    if snap["state"] in UNSAFE:
        return False, f"围栏状态为 {snap['state']}，已拦截下发", snap

    return True, "围栏状态安全", snap
