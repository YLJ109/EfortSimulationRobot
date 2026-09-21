# -*- coding: utf-8 -*-
"""
采集器: 后台线程读取关节角(真实 Modbus 或合成模拟) -> 算 TCP -> 广播 + 入库。
单例 collector 由 main.py 在启动时 start(), 关闭时 stop()。
"""
from __future__ import annotations

import time
from threading import Lock, Thread
from typing import Any, Dict, Optional

from app.core.config import get_config
from app.core.logger import get_logger
from app.db.database import SessionLocal
from app.db.crud import insert_pose
from app.services.hub import hub
from app.services.kinematics import simulate_pose, tcp_of
from app.services.modbus import ModbusRobot

log = get_logger("collector")


class Collector:
    def __init__(self) -> None:
        self.modbus = ModbusRobot()
        self.latest: Optional[Dict[str, Any]] = None
        self.simulated = False
        self.connected = False
        self._lock = Lock()
        self._running = False
        self._thread: Optional[Thread] = None
        self._last_db_write = 0.0
        self._force_reconnect = False   # 手动重连标志（网线切换后由 API 触发）

    def decide_mode(self) -> bool:
        """返回是否模拟: always / never / auto(不可达则模拟)。"""
        mode = str(get_config().connection.get("simulate", "auto")).lower()
        if mode == "always":
            return True
        if mode == "never":
            return False
        ok = self.modbus.reachable()
        self.connected = ok
        return not ok

    def start(self) -> None:
        self.simulated = self.decide_mode()
        self.connected = not self.simulated
        log.info("采集器启动, 模式=%s", "SIMULATE" if self.simulated else "REAL")
        self._running = True
        self._thread = Thread(target=self._loop, name="collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def get_latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.latest

    def reconnect(self) -> Dict[str, Any]:
        """手动重连（网线在摄像头/机器人间切换后触发）。

        立即探测 Modbus 可达性并切换真实/模拟模式，同时通知采集循环重置
        失败计数，无需重启服务。
        """
        ok = self.modbus.reachable()
        with self._lock:
            if ok:
                self.simulated = False
                self.connected = True
            else:
                self.simulated = True
                self.connected = False
            self._force_reconnect = True   # 让 _loop 重置 fail_count/retry_at
        log.info("手动重连: 控制器%s", "可达, 切回真实" if ok else "不可达, 保持模拟")
        return {"connected": self.connected, "simulated": self.simulated}

    def _loop(self) -> None:
        cfg = get_config()
        samp = cfg.sampling
        read_hz = float(samp.get("read_hz", 10))
        push_hz = float(samp.get("ws_push_hz", 20))
        db_hz = float(samp.get("db_write_hz", 2))
        loop_freq = max(read_hz, push_hz)
        interval = 1.0 / loop_freq
        read_interval = 1.0 / max(read_hz, 0.5)   # P0-2: 真实读取按 read_hz 节流
        db_interval = 1.0 / max(db_hz, 0.1)
        t0 = time.time()

        # 轴符号校准 (config/robot.yaml: axis_sign), 界面转向与真机不符时可逐轴翻转
        signs = [float(s) for s in cfg.get("axis_sign", default=[1] * 6)]
        if len(signs) < 6:
            signs = signs + [1.0] * (6 - len(signs))

        # P0-1: 读取失败不永久降级 —— 仅累计; 连续失败到阈值才临时模拟, 并持续自动重试。
        # 阈值调大(read_hz=10 时约 3 秒): 避免控制器重启/网络抖动等短暂中断被误判为永久掉线,
        # 减少"时好时坏"的频繁降级/恢复抖动。
        FAIL_LIMIT = 30
        last_read = 0.0
        fail_count = 0
        retry_at = 0.0
        last_joints = simulate_pose(0.0)

        while self._running:
            loop_start = time.time()
            t = loop_start - t0
            joints = None

            # 0) 手动重连请求: 模式已由 reconnect() 同步更新, 这里仅重置失败计数/重试定时
            with self._lock:
                if self._force_reconnect:
                    self._force_reconnect = False
                    fail_count = 0
                    retry_at = 0.0

            # 1) 处于模拟(初始离线 或 降级中): 定期探测能否恢复真实
            if self.simulated:
                if loop_start >= retry_at:
                    retry_at = loop_start + 5.0
                    if self.modbus.reachable():
                        log.info("控制器恢复可达, 切回真实读取")
                        self.simulated = False
                        fail_count = 0
                if self.simulated:
                    joints = simulate_pose(t)
                    self.connected = False

            # 2) 真实读取 (按 read_hz 节流; 未到读取时刻复用上次值, 画面平滑)
            if joints is None:
                if loop_start - last_read >= read_interval:
                    last_read = loop_start
                    rj, err = self.modbus.read_pose()
                    if err is None:
                        fail_count = 0
                        self.connected = True
                        last_joints = rj
                        joints = rj
                    else:
                        fail_count += 1
                        self.connected = False
                        if fail_count >= FAIL_LIMIT:
                            log.warning("连续 %d 次读取失败: %s -> 临时降级模拟(将持续自动重试)",
                                        fail_count, err)
                            self.simulated = True
                            retry_at = loop_start + 3.0
                        else:
                            log.warning("Modbus 读取失败(%d/%d): %s", fail_count, FAIL_LIMIT, err)
                        joints = simulate_pose(t)
                else:
                    joints = last_joints

            joints = [j * s for j, s in zip(joints, signs)]

            try:
                tcp = tcp_of(joints)
            except Exception as e:
                log.error("运动学计算失败: %s", e)
                tcp = (0.0, 0.0, 0.0)

            payload = {
                "type": "pose",
                "j1": joints[0], "j2": joints[1], "j3": joints[2],
                "j4": joints[3], "j5": joints[4], "j6": joints[5],
                "tcp": {"x": tcp[0], "y": tcp[1], "z": tcp[2]},
                "simulated": self.simulated,
                "t": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            }
            with self._lock:
                self.latest = payload
            hub.broadcast(payload)

            now = time.time()
            if now - self._last_db_write >= db_interval:
                self._last_db_write = now
                self._db_write(joints, tcp)

            elapsed = time.time() - loop_start
            sleep_t = interval - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    def _db_write(self, joints, tcp) -> None:
        s = SessionLocal()
        try:
            insert_pose(
                s, joints[0], joints[1], joints[2],
                joints[3], joints[4], joints[5], tcp,
                source="sim" if self.simulated else "real",
            )
        except Exception as e:
            log.error("入库失败: %s", e)
        finally:
            s.close()


collector = Collector()
