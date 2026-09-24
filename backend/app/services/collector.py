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
from app.services.events import emit as emit_event
from app.services.hub import hub
from app.services.kinematics import simulate_pose, tcp_of
from app.services.modbus import ModbusRobot
from app.services.motion import motion, real_write_enabled
from app.services.runmode import runmode
from app.services.sim_robot import sim_robot

log = get_logger("collector")

# 寄存器快照（rc-status）并入 WS 的广播周期：与旧前端 4s 轮询节奏一致，
# 点到 / 适度 —— rc_snapshot 是 3 个 FC3 事务，太高会拖采集循环，太低则点动体验迟钝。
RC_STATUS_INTERVAL = 4.0


class Collector:
    def __init__(self) -> None:
        self.modbus = ModbusRobot()
        self.latest: Optional[Dict[str, Any]] = None
        self.latest_rc_status: Optional[Dict[str, Any]] = None   # 最近一帧 rc_status（供 WS 握手即时推）
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
        # ★ 与自动恢复保持一致：显式 simulate=always 时，手动重连也不切真实，
        #   否则演示/离线调试环境点一下"重连"就被拽回真机链路。
        mode = str(get_config().connection.get("simulate", "auto")).lower()
        ok = False if mode == "always" else self.modbus.reachable()
        with self._lock:
            if ok:
                self.simulated = False
                self.connected = True
            else:
                self.simulated = True
                self.connected = False
            self._force_reconnect = True   # 让 _loop 重置 fail_count/retry_at
        log.info("手动重连: 控制器%s%s",
                 "可达, 切回真实" if ok else "不可达, 保持模拟",
                 " (配置 simulate=always，已锁定模拟)" if mode == "always" else "")
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

        # 是否允许自动切回真实链路：显式 simulate=always 时禁止（见下方第 1 步）
        sim_mode = str(get_config().connection.get("simulate", "auto")).lower()
        allow_real = sim_mode != "always"

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
        # ★ Stage C：模拟初值从零位开始（不再是无意义的正弦扫掠起点），
        #   有指令后由 sim_robot 推着走。
        last_joints = [0.0] * 6
        sim_tracking = False
        last_rc_at = 0.0

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
            #    ★ 只有 simulate=auto/never 才自动切回真实；显式 simulate=always
            #      （演示、离线调试、以及测试环境）必须"锁死"在模拟，否则采集线程
            #      一发现控制器可达就把用户强制的模拟模式顶掉。
            if self.simulated:
                if loop_start >= retry_at:
                    retry_at = loop_start + 5.0
                    if allow_real and self.modbus.reachable():
                        log.info("控制器恢复可达, 切回真实读取")
                        self.simulated = False
                        fail_count = 0
                        emit_event("connection", "info", "connection.restored",
                                   "Modbus 控制器恢复可达，已切回真实链路",
                                   {"host": self.modbus.host if hasattr(self.modbus, "host") else ""})
                if self.simulated:
                    # ★ Stage C：有状态仿真机——没有指令就停在原地，有指令真的走过去。
                    joints, sim_tracking = sim_robot.step(loop_start)
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
                            emit_event("connection", "warn", "connection.degraded",
                                       f"连续 {fail_count} 次读取失败，临时降级为模拟",
                                       {"fail_count": fail_count, "error": str(err)[:200]})
                        else:
                            log.warning("Modbus 读取失败(%d/%d): %s", fail_count, FAIL_LIMIT, err)
                        # 真实模式的短暂补帧：仍用扫掠，但不进入 tracking 语义
                        joints = simulate_pose(t)
                        sim_tracking = False
                else:
                    joints = last_joints
                    sim_tracking = False

            joints = [j * s for j, s in zip(joints, signs)]

            try:
                tcp = tcp_of(joints)
            except Exception as e:
                log.error("运动学计算失败: %s", e)
                tcp = (0.0, 0.0, 0.0)

            # ★ Stage C：WS 帧携带指令通道 —— 前端"模拟未走位时显示指令目标"、
            #   "走位中显示仿真体位"的判据就吃这三个字段（Stage A 已接好）。
            mst = motion.state()
            cmd = mst.get("last_target")
            cmd_tcp = None
            if cmd:
                try:
                    ct = tcp_of(cmd)
                    cmd_tcp = {"x": ct[0], "y": ct[1], "z": ct[2]}
                except Exception:
                    cmd_tcp = None
            tracking = bool(sim_tracking) if self.simulated else False

            payload = {
                "type": "pose",
                "j1": joints[0], "j2": joints[1], "j3": joints[2],
                "j4": joints[3], "j5": joints[4], "j6": joints[5],
                "tcp": {"x": tcp[0], "y": tcp[1], "z": tcp[2]},
                "simulated": self.simulated,
                "cmd": cmd,
                "cmd_tcp": cmd_tcp,
                "tracking": tracking,
                "t": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            }
            with self._lock:
                self.latest = payload
            hub.broadcast(payload)

            # rc-status：低频并入 WS 推流（省去前端一路 HTTP 轮询）
            if loop_start - last_rc_at >= RC_STATUS_INTERVAL:
                last_rc_at = loop_start
                self._publish_rc_status()

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

    @staticmethod
    def _service_program() -> int:
        """点动服务程序号（现场=200/JOGSVC）；与 robot.py::rc_status 口径一致。"""
        try:
            return int(get_config().get("motion", "jog", "service_program", default=0) or 0)
        except Exception:
            return 0

    def _publish_rc_status(self) -> None:
        """低频（RC_STATUS_INTERVAL）把控制器寄存器快照广播进 WS，并缓存供握手即时推。

        与 /api/rc-status 字段保持一致，让前端 WS 帧与 HTTP 种子可共用一套解析。
        ★ 模拟/离线时不做真实读 —— rc_snapshot 会 TCP 连接，失败会超时阻塞采集循环。
        """
        t_now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        if self.simulated:
            payload = {"type": "rc_status", "ok": False, "simulated": True,
                       "error": "控制器模拟/离线，读不到寄存器快照",
                       "real_enabled": real_write_enabled(),
                       "service_program": self._service_program(), "t": t_now}
            self.latest_rc_status = payload
            hub.broadcast(payload)
            return
        snap, err = self.modbus.rc_snapshot()
        if snap is None:
            payload = {"type": "rc_status", "ok": False, "simulated": False,
                       "error": err or "读不到控制器",
                       "real_enabled": real_write_enabled(),
                       "service_program": self._service_program(), "t": t_now}
        else:
            b = snap["bits"]
            snap["ok"] = True
            snap["real_enabled"] = real_write_enabled()
            snap["service_program"] = self._service_program()
            snap["ready"] = bool(b["servo"] and b["prog_loaded"] and b["run"]
                                 and (b["auto"] or b["remote"])
                                 and not b["alarm"] and not b["estop"])
            # ★ 顺手把实测档位喂给 runmode：底栏「示教器」灯从此自动确认，
            #   不必再让操作员按旋钮位置手动声明一遍（见 services/runmode.py）。
            feed_run_mode(snap.get("mode"), snap.get("status_word"))
            payload = {"type": "rc_status", "t": t_now, **snap}
        self.latest_rc_status = payload
        hub.broadcast(payload)


def feed_run_mode(mode: Optional[str], status_word: Optional[int] = None) -> None:
    """控制器实测档位 → runmode（变化时留一条事件，便于事后追"谁把旋钮拨走了"）。

    模块级函数而不是 Collector 的方法：`/api/rc-status`（robot.py）也会在
    "采集器锁死模拟、但用户手动点了一下真机链路刷新"时读到真快照，需要走同一条路。
    """
    r = runmode.observe(mode or "")
    if r.get("changed"):
        emit_event("control", "info", "control.run_mode_observed",
                   f"控制器实测档位：{r['mode']}"
                   + (f"（原 {r['previous']}）" if r.get("previous") else ""),
                   {"mode": r["mode"], "previous": r.get("previous") or "",
                    "raw": mode, "status_word": status_word})


collector = Collector()
