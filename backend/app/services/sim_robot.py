# -*- coding: utf-8 -*-
"""
有状态仿真机体位（阶段 7 Stage C）。

## 为什么必须有它（改造前的问题）

模拟模式下后端推的是 `kinematics.simulate_pose(t)` —— 一个**与指令完全无关**的
时间正弦扫掠。前端无论下发什么指令，"机器人现在的位置"都在按自己的节拍摆动，
三页的 3D/读数根本无法反映"我下发的目标到底执行了没有"。前端只好发明
localDemo 之类的抑制开关来对抗它，越描越黑（这正是阶段 7 三个根因之一）。

## 现在的模型

SimRobot 维护自己的关节状态：
  - `motion.command()`（点动/单点/程序全走这一个入口）在模拟模式下把目标
    交给本模块 → `on_command()` 记录起点/目标/时长；
  - 采集循环每帧调 `step()` → 按固定基准角速度**线性走向目标**；
  - 到位后 tracking=False，体位停在目标处（而不是被扫掠拽走）。

于是模拟模式与真机同构：指令 → 走位过程（tracking=true）→ 到位停住。
前端 displayQ 的"指令即位置"兜底分支只在 tracking=false 且有 cmd 时生效，
与本模块严格互补，互不抢戏。

★ 时长估算：dur = 最大轴位移 / (45°/s × speed_pct/100)，最短 0.4s ——
  太短人眼看不出"走过去"的过程，太短与"瞬间跳变"无异。
"""
from __future__ import annotations

import time
from threading import Lock
from typing import List, Optional, Tuple

from app.core.logger import get_logger

log = get_logger("sim_robot")

RATE_DPS = 45.0        # 基准角速度（speed_pct=100 时），deg/s
MIN_DURATION_S = 0.4   # 单次走位最短时长（保证"走过去"肉眼可见）


class SimRobot:
    def __init__(self) -> None:
        self._lock = Lock()
        self._q: List[float] = [0.0] * 6          # 当前体位
        self._from: Optional[List[float]] = None  # 本次走位起点
        self._target: Optional[List[float]] = None
        self._t0 = 0.0
        self._dur = 0.0
        self._cmd_seq = 0                          # 收到过几次指令（测试/排障用）

    # ---------------- 指令入口（motion.command 模拟分支调用） ----------------
    def on_command(self, target: List[float], speed_pct: int = 100) -> None:
        """记录一次目标走位。目标按当前限位外的值也照收 —— 校验是 motion 的事。

        ★ 收新指令前先把进行中的走位推进到当前时刻：否则"上一发还没走完就被
          新目标替换"时，起点会从半路**回跳**到旧起点（体位瞬移）。
        """
        t = [float(x) for x in (list(target) + [0.0] * 6)[:6]]
        with self._lock:
            if self._target is not None:
                self._advance_locked(time.time())
            q0 = self._q[:]
            max_deg = max(abs(t[i] - q0[i]) for i in range(6))
            sp = min(100.0, max(1.0, float(speed_pct or 100))) / 100.0
            dur = max(MIN_DURATION_S, max_deg / (RATE_DPS * sp))
            self._from = q0
            self._target = t
            self._t0 = time.time()
            self._dur = dur
            self._cmd_seq += 1

    # ---------------- 采集循环每帧调用 ----------------
    def _advance_locked(self, now: float) -> None:
        """推进内部状态（须持锁）。到位时钉在目标上并清 target。"""
        if self._target is None:
            return
        k = (now - self._t0) / self._dur if self._dur > 0 else 1.0
        if k >= 1.0:
            self._q = self._target[:]
            self._from = None
            self._target = None
            return
        f, t = self._from, self._target
        self._q = [f[i] + (t[i] - f[i]) * k for i in range(6)]

    def step(self, now: Optional[float] = None) -> Tuple[List[float], bool]:
        """推进体位，返回 (当前六轴角度, 是否正在走位)。线程安全。"""
        with self._lock:
            if self._target is None:
                return self._q[:], False
            now = time.time() if now is None else now
            tracking = now < (self._t0 + self._dur)
            self._advance_locked(now)
            if not tracking:      # 刚好走完：target 已被清
                return self._q[:], False
            return self._q[:], True

    # ---------------- 其它 ----------------
    def reset(self, q: Optional[List[float]] = None) -> None:
        """归零/重置（急停不清位姿 —— 急停只挡新指令，不瞬移已到位的位姿）。"""
        with self._lock:
            self._q = [float(x) for x in (q or [0.0] * 6)][:6]
            self._from = None
            self._target = None

    def state(self) -> dict:
        with self._lock:
            return {
                "q": self._q[:],
                "target": self._target[:] if self._target else None,
                "cmd_seq": self._cmd_seq,
            }


sim_robot = SimRobot()
