# -*- coding: utf-8 -*-
"""
执行引擎: 把目标关节角下发给机器人(真实模式写 Modbus)或安全模拟(默认)。

安全设计:
  - 所有写接口都必须经 require_control 鉴权(见 api/control.py)。
  - 急停优先: 触发后任何 command 都被拒绝, 直到 reset_estop。
  - 限位校验: 目标超出关节限位直接拒绝。
  - 真实下发默认关闭: 需同时设置环境变量 EFORT_REAL_MOTION=1 且
    config motion.real_write=true, 否则只做校验+记录(前端据此动画)。
"""
from __future__ import annotations

import os
import time
from threading import Lock
from typing import List, Optional

from app.core.config import get_config
from app.core.logger import get_logger
from app.services.modbus import ADDR_SET_SPEED, ModbusRobot
from app.services.sim_robot import sim_robot

log = get_logger("motion")

# ★ 2026-09-24：这里**不能**顶层 import collector —— collector 顶层要 import
#   motion（读指令通道 + real_write_enabled），顶层互引会让 uvicorn 起不来
#   （ImportError: cannot import name 'collector' from partially initialized module）。
#   collector 只在 command() 的 J6 安全锁分支里用到，且那一支是真机下发路径，
#   改为函数内延迟导入，导入时机上 collector 早已初始化完毕。


def real_motion_env_active() -> bool:
    """★ P1-2：环境变量真值表唯一实现（设置页 runtime_flags 与执行引擎共用）。

    改造前两处各写一份且不一致：runtime_flags 认 true/yes/on，执行引擎只认 "1"
    → "真实下发已生效"指示灯会撒谎。现在只有这一份。
    """
    return os.environ.get("EFORT_REAL_MOTION", "0").strip().lower() in ("1", "true", "yes", "on")


def real_write_enabled() -> bool:
    """双确认总闸：环境变量 + config motion.real_write。任意一个没开都是安全模拟。"""
    return bool(real_motion_env_active()) and bool(
        get_config().get("motion", "real_write", default=False)
    )


class MotionService:
    def __init__(self) -> None:
        # 真实下发开关: 环境变量 + 配置双重确认, 默认 False(安全模拟)
        # ★ 真值表统一走 real_motion_env_active()（P1-2：两处不一致会让指示灯撒谎）
        self.real = real_write_enabled()
        self._lock = Lock()
        self.moving = False
        self.stopped = False
        self.last_target: Optional[List[float]] = None
        self.last_executed_at = 0.0
        self.last_error: Optional[str] = None
        self.modbus = ModbusRobot()
        log.info("执行引擎就绪, 模式=%s", "REAL" if self.real else "SIMULATE(安全模拟)")

    def _limits(self) -> List[tuple]:
        lim = get_config().get("joint_limits", default=[])
        if lim:
            return [(float(l["min"]), float(l["max"])) for l in lim]
        return [(-180.0, 180.0)] * 6

    def _in_limits(self, q: List[float]) -> bool:
        for v, (lo, hi) in zip(q, self._limits()):
            if v < lo - 1e-6 or v > hi + 1e-6:
                return False
        return True

    def command(self, joints: List[float], speed_pct: int = 100,
                dwell_ms: int = 0) -> dict:
        """执行一次目标到位。返回执行结果(含模式/是否真下发)。"""
        with self._lock:
            if self.stopped:
                return {"ok": False, "estop": True, "error": "急停已触发，请先复位急停"}
            if not self._in_limits(joints):
                return {"ok": False, "error": "目标超出关节限位", "violations": True}
            # ★ 安全锁 J6-ONLY：只允许第 6 轴变化，J1–J5 必须与当前保持锁定。
            #   这是"仅用 J6 做动作测试"的硬约束，任何请求想动 J1–J5 一律拒绝。
            if self.real:
                from app.services.collector import collector  # 延迟导入防循环
                cur_p = collector.get_latest()
                if cur_p:
                    cur_j = [float(cur_p.get(f"j{i}", 0.0)) for i in range(1, 7)]
                else:
                    cur_j = [float(v) for v in joints]
                locked = 0.5  # ° 容差
                for i in range(5):
                    dev = abs((joints[i] or 0.0) - cur_j[i])
                    if dev > locked:
                        self.moving = False
                        return {"ok": False,
                                "error": f"J{i+1} 被锁定（仅允许 J6 做动作测试），偏差 {dev:.2f}° 已拒绝",
                                "lock_j6": True, "current": cur_j}
            self.moving = True
            self.last_target = [round(float(x), 3) for x in joints]
            self.last_error = None

        mode = "real" if self.real else "sim"
        if self.real:
            # ★ Stage D：真实下发走"点动通道"（实测确认的唯一有效路径）——
            #   速度设定(40103) → 目标角(40139~44, FC6 逐发)+回读校验 → settle 0.15s
            #   → 触发(40135.Bit0) → 等完成(40035.Bit0) → 撤触发。
            #   控制器必须有常驻点动服务程序（200/JOGSVC）在 WAIT 挂起。
            with self._lock:
                speed = max(1, min(100, int(speed_pct)))
            # ★ 速度寄存器校正（2026-09-24 实机探测复验）：
            #   速度设定 = 0 基址 102 = 手册 40103（modbus.ADDR_SET_SPEED）。
            #   本行原写死 103 → 实际落到 40104「目标程序号」，既没设成速度、
            #   又把程序号寄存器冲成了速度值（探测时 40103=50 而 40104=200 即为证）。
            #   必须用常量 ADDR_SET_SPEED，不再写裸地址。
            ok, err = self.modbus.write_reg(ADDR_SET_SPEED, speed)
            if not ok:
                with self._lock:
                    self.moving = False
                    self.last_error = err
                return {"ok": False, "mode": mode, "error": "写速度设定失败: %s" % err}
            # ★ should_abort：急停置位后，飞行中的点动链路（写目标→触发→轮询
            #   完成位，最长 30s）能立刻感知并撤触发中止，而不是排队走完。
            #   _lock 不可重入且不能在 I/O 期间持有，用短锁快照读 stopped。
            ok, err, detail = self.modbus.rc_jog_execute(
                self.last_target, speed, should_abort=self._stopped_now)
            with self._lock:
                self.moving = False
                self.last_executed_at = time.time()
                if not ok:
                    self.last_error = err
                    return {"ok": False, "mode": mode, "error": err or "下发失败",
                            "detail": detail}
            return {"ok": True, "target": self.last_target, "speed_pct": speed,
                    "mode": mode, "executed": True, "detail": detail}

        # 模拟模式: 仅校验 + 记录；★ Stage C：同时把目标交给有状态仿真机，
        #   采集循环会推着仿真机体位真的"走向"目标（tracking=true → 到位停住），
        #   三页的 3D/读数因此能反映指令的执行过程，而不再是无关的正弦扫掠。
        sim_robot.on_command(self.last_target, speed_pct)
        time.sleep(min(dwell_ms / 1000.0, 0.05))
        with self._lock:
            self.moving = False
            self.last_executed_at = time.time()
        return {"ok": True, "target": self.last_target, "speed_pct": speed_pct,
                "mode": mode, "executed": False}

    def _stopped_now(self) -> bool:
        """短锁快照读急停标志（rc_jog_execute 的 should_abort 回调用）。"""
        with self._lock:
            return self.stopped

    def estop(self) -> dict:
        with self._lock:
            self.stopped = True
            self.moving = False
        if self.real:
            try:
                # ★ Stage D：急停 = 0x1005 停止命令字（旧 estop_addr 方案已证伪）
                ok, err = self.modbus.rc_estop(True)
                if not ok:
                    log.error("急停下发失败: %s", err)
            except Exception as e:  # noqa
                log.error("急停下发异常: %s", e)
        else:
            log.warning("急停(模拟): 已锁定执行引擎")
        return self.state()

    def reset_estop(self) -> dict:
        with self._lock:
            self.stopped = False
        if self.real:
            try:
                self.modbus.rc_estop(False)
            except Exception as e:  # noqa
                log.error("急停复位下发异常: %s", e)
        return self.state()

    def state(self) -> dict:
        with self._lock:
            return {
                "moving": self.moving,
                "stopped": self.stopped,
                "mode": "real" if self.real else "sim",
                "last_target": self.last_target,
                "last_executed_at": self.last_executed_at,
                "last_error": self.last_error,
            }


motion = MotionService()
