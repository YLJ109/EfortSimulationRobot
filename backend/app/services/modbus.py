# -*- coding: utf-8 -*-
"""
Robox 控制器 Modbus TCP 只读客户端。
协议与已验证的 robot_pose.py 完全一致:
  FC3, 起始地址 10, 读 12 个保持寄存器;
  每关节 2 寄存器组成 float32(大端, 字序交换: 高字在后)。
  关节角单位: 度。
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_config
from app.core.logger import get_logger

log = get_logger("modbus")

# ======================================================================
# ★ 实测确认的寄存器表（2026-09-23，详见 docs/控制器Modbus寄存器勘察报告.md）
#   手册 4xxxx = 0 基址 + 1。
READ_BLOCK = 32          # 系统区一次读 32 最稳（qty=23 确定性失败；白名单 1..8,10,12,16,20,24,32,48,64）
RO_WINDOW = 10           # 点动用户区 40135~40144 单读窗口
ADDR_STATUS = 0          # 40001 状态位
ADDR_SPEED = 2           # 40003 运行速度 %
ADDR_ALARM1 = 3          # 40004 报警代码 1
ADDR_ALARM2 = 4          # 40005 报警代码 2
ADDR_PROG = 5            # 40006 当前程序号
ADDR_JOINT1 = 10         # 40011~22 J1~J6 FLOAT（CDAB 字序）
ADDR_CMD = 100           # 40101 指令字（FC6 单条写入）
ADDR_SET_SPEED = 102     # 40103 速度设定（%）
ADDR_SET_PROG = 103      # 40104 目标程序号
ADDR_RO_TRIG = 134       # 40135 PC→机器人位区（Bit0 = 点动触发，电平）
ADDR_JOG_ANG = 138       # 40139~44 J1~J6 目标绝对角 ×100（int16 补码）
ADDR_WO_STAT = 34        # 40035 机器人→PC 位区（Bit0 = 点动完成）
ANG_SCALE = 100

# 命令字：必须同沿单条写入，且始终保留 Bit0(上伺服)+Bit12(伺服使能)
CMD_SERVO = 0x1001
CMD_LOAD = 0x1011
CMD_RUN = 0x1013
CMD_STOP = 0x1005
CMD_CLEAR = 0x1009
CMD_DISABLE = 0x2000
CMD_ZERO = 0x0000
SERVO_REENGAGE_DELAY = 0.6   # 全清→重上电的等待（实测吸合延迟 ≈0.55s）

# 状态位定义（reg0）
STATUS_BITS = {
    "manual": 0, "auto": 1, "remote": 2, "servo": 3, "alarm": 4,
    "estop": 5, "run": 6, "safe1": 7, "safe2": 8, "safe3": 9, "safe4": 10,
    "prog_loaded": 11, "servo_ready": 12, "prog_reserve": 13, "prog_reset": 14,
}


def _build(func: int, addr: int, qty: int, uid: int = 1, tid: int = 1) -> bytes:
    pdu = struct.pack(">BHH", func, addr, qty)
    return struct.pack(">HHHB", tid, 0, len(pdu) + 1, uid) + pdu


def _words_to_float(low: int, high: int) -> float:
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


def _float_to_words(f: float) -> Tuple[int, int]:
    """float32 大端 ↔ 两寄存器, 与 read_pose 的字节序严格一致(低字在前)。"""
    b = struct.pack(">f", float(f))
    high = (b[0] << 8) | b[1]
    low = (b[2] << 8) | b[3]
    return low, high


class ModbusRobot:
    def __init__(self) -> None:
        cfg = get_config()
        conn = cfg.connection
        mb = cfg.modbus
        self.host = conn.get("host", "192.168.1.12")
        self.port = int(conn.get("port", 502))
        self.uid = int(conn.get("unit_id", 1))
        self.timeout = float(conn.get("timeout_s", 2.0))
        self.func = int(mb.get("func", 3))
        self.base_addr = int(mb.get("base_addr", 10))
        self.count = int(mb.get("count", 12))
        self._sock: Optional[socket.socket] = None    # P1-1: 复用长连接
        # ★ 采集线程（read_pose）与执行线程（点动链路多条 FC6/FC3）共用同一 socket，
        #   TCP 是纯字节流，并发 send/recv 会串帧 —— 所有 IO 必须持锁整帧完成。
        self._io_lock = threading.Lock()

    def reachable(self) -> bool:
        """快速 TCP 探测, 用于决定模拟/真实模式。"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            rc = s.connect_ex((self.host, self.port))
            s.close()
            return rc == 0
        except Exception:
            return False

    def _close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _ensure_sock(self) -> socket.socket:
        if self._sock is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.host, self.port))
            self._sock = s
        return self._sock

    def _recv_frame(self, s: socket.socket) -> bytes:
        """收全一个 Modbus TCP 帧: MBAP 头 6 字节 + length 指示的后续字节。"""
        buf = b""
        while len(buf) < 6:
            chunk = s.recv(6 - len(buf))
            if not chunk:
                raise ConnectionError("连接被关闭")
            buf += chunk
        length = struct.unpack(">H", buf[4:6])[0]
        total = 6 + length
        while len(buf) < total:
            chunk = s.recv(total - len(buf))
            if not chunk:
                raise ConnectionError("响应不完整")
            buf += chunk
        return buf

    def read_pose(self) -> Tuple[Optional[List[float]], Optional[str]]:
        """返回 (6 个关节角 deg, None) 或 (None, 错误信息)。复用长连接。"""
        try:
            with self._io_lock:               # ★ 与点动写链路共用 socket，必须整帧持锁
                s = self._ensure_sock()
                s.sendall(_build(self.func, self.base_addr, self.count, self.uid))
                data = self._recv_frame(s)
        except Exception as e:
            self._close()          # 出错断开, 下次重建
            return None, "timeout/error: %s" % e

        if len(data) < 9:
            return None, "响应过短 %d 字节" % len(data)
        fc = data[7]
        if fc & 0x80:
            code = data[8] if len(data) > 8 else -1
            return None, "Modbus 异常 0x%02x" % code
        if fc not in (3, 4):
            return None, "非预期功能码 0x%02x" % fc

        bc = data[8]
        regs = struct.unpack(">%dH" % (bc // 2), data[9:9 + bc])
        if len(regs) < 12:
            return None, "寄存器不足 %d" % len(regs)
        pose = [_words_to_float(regs[i * 2], regs[i * 2 + 1]) for i in range(6)]
        return pose, None

    # ==================================================================
    # 实机寄存器协议层（2026-09-23 实测确认；写路径 Stage D 重写）
    #
    # 旧 write_target（FC16 → addr 30）已**实机证伪**：地址 30..41 恒 0，
    # 根本不是目标关节角区。真实下发走"点动通道"：
    #   PC 写 40139~44 目标绝对角(×100 int16, 逐寄存器 FC6)
    #   → 回读校验 → 稳定 0.15s（防竞态：不等待就会触发上一发目标）
    #   → 置 40135.Bit0 触发 → 等 40035.Bit0 完成 → 撤触发。
    # 控制器侧必须有一个常驻点动服务程序（现场 = 200 / JOGSVC）在 WAIT 挂起。
    # ==================================================================
    @staticmethod
    def encode_angle(deg: float) -> int:
        """角度 → 有符号 16 位（×100 补码）。量化 0.01°。"""
        return int(round(float(deg) * ANG_SCALE)) & 0xFFFF

    @staticmethod
    def decode_angle(word: int) -> float:
        v = word - 0x10000 if word > 0x7FFF else word
        return v / ANG_SCALE

    def read_regs(self, addr: int, qty: int) -> Tuple[Optional[List[int]], Optional[str]]:
        """FC3 读任意寄存器区。返回 (寄存器列表, None) 或 (None, 错误)。"""
        try:
            with self._io_lock:
                s = self._ensure_sock()
                s.sendall(_build(3, int(addr), int(qty), self.uid))
                data = self._recv_frame(s)
        except Exception as e:
            self._close()
            return None, "timeout/error: %s" % e
        if len(data) < 9:
            return None, "响应过短 %d 字节" % len(data)
        fc = data[7]
        if fc & 0x80:
            return None, "Modbus 异常 0x%02x" % (data[8] if len(data) > 8 else 0)
        if fc != 3:
            return None, "非预期功能码 0x%02x" % fc
        bc = data[8]
        regs = struct.unpack(">%dH" % (bc // 2), data[9:9 + bc])
        return list(regs), None

    def write_reg(self, addr: int, value: int) -> Tuple[Optional[int], Optional[str]]:
        """FC6 写单寄存器。返回 (回显值, None) 或 (None, 错误)。★ 回显在 body[3:5]。"""
        try:
            with self._io_lock:
                s = self._ensure_sock()
                pdu = struct.pack(">BHH", 6, int(addr), int(value) & 0xFFFF)
                s.sendall(struct.pack(">HHHB", 1, 0, len(pdu) + 1, self.uid) + pdu)
                data = self._recv_frame(s)
        except Exception as e:
            self._close()
            return None, "写异常: %s" % e
        if len(data) < 9:
            return None, "写响应过短 %d 字节" % len(data)
        fc = data[7]
        if fc & 0x80:
            return None, "Modbus 异常 0x%02x" % (data[8] if len(data) > 8 else 0)
        if fc != 6:
            return None, "非预期功能码 0x%02x" % fc
        return struct.unpack(">H", data[9:11])[0] if len(data) >= 11 else None, None

    def rc_command(self, word: int) -> Tuple[Optional[int], Optional[str]]:
        """写 40101 指令字。★ 必须同沿单条写入（保留 Bit0+Bit12）。"""
        return self.write_reg(ADDR_CMD, int(word) & 0xFFFF)

    def rc_snapshot(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """读一帧完整快照：系统区 32 + 点动用户区 10 + 完成位 1。

        ★ 40135~44 是合法单读窗口（10 个）：[0]=触发位区、[4..9]=目标角×100；
          40035 完成位单独一发（qty=1 在白名单内）。
        """
        regs, err = self.read_regs(0, READ_BLOCK)
        if regs is None:
            return None, err
        ro, err2 = self.read_regs(ADDR_RO_TRIG, RO_WINDOW)
        wo, err3 = self.read_regs(ADDR_WO_STAT, 1)
        st = regs[ADDR_STATUS]
        bits = {k: (st >> b) & 1 for k, b in STATUS_BITS.items()}
        return {
            "status_word": st,
            "bits": bits,
            "mode": ("manual" if bits["manual"] else
                     "auto" if bits["auto"] else
                     "remote" if bits["remote"] else "unknown"),
            "speed_pct": regs[ADDR_SPEED],
            "alarm1": regs[ADDR_ALARM1],
            "alarm2": regs[ADDR_ALARM2],
            "prog": regs[ADDR_PROG],
            "joints": [_words_to_float(regs[ADDR_JOINT1 + i * 2],
                                       regs[ADDR_JOINT1 + i * 2 + 1]) for i in range(6)],
            "jog_trig": bool(ro[0] & 0x0001) if ro else None,
            "jog_target_raw": ro[4:10] if ro else None,
            "jog_done": (bool(wo[0] & 0x0001) if wo else None),
            "errors": [e for e in (err2, err3) if e],
        }, None

    def rc_write_jog_target(self, joints: List[float]) -> Tuple[bool, Optional[str]]:
        """写 6 个目标绝对角到 40139~44（6 发 FC6）+ 回读校验。不置触发位。"""
        for i, a in enumerate(joints[:6]):
            _, err = self.write_reg(ADDR_JOG_ANG + i, self.encode_angle(a))
            if err:
                return False, "写目标角 J%d 失败: %s" % (i + 1, err)
        ro, err = self.read_regs(ADDR_RO_TRIG, RO_WINDOW)
        if ro is None:
            return False, "目标角回读失败: %s" % err
        for i in range(6):
            want = self.encode_angle(joints[i])
            got = ro[4 + i] if 4 + i < len(ro) else None
            if got != want:
                return False, ("目标角回读不一致 J%d：写 %d 读 %s"
                               % (i + 1, want, got))
        return True, None

    def rc_jog_execute(self, joints: List[float], speed_pct: int = 100,
                       on_event=None, should_abort=None) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """完整点动链路（同步）：校验触发位 → 写目标+回读 → settle → 触发 → 等完成 → 撤触发。

        调用方需已通过 real_write + EFORT_REAL_MOTION 双确认（见 motion.py）。
        on_event(step, msg) 可选，用于逐拍上报进度。
        should_abort() 可选，急停回调：在触发前与等待完成轮询中检查，
        为 True 时立即撤触发并中止（不等待本次点动走完）—— 急停必须能
        打断飞行中的点动，而不是排队等它自然结束（最长可达 30s）。
        """
        detail: Dict[str, Any] = {"steps": []}
        ev = (lambda step, msg: (detail["steps"].append({"step": step, "msg": msg}),
                                 on_event(step, msg) if on_event else None)[1])

        # 0) 前置守卫：触发位必须空闲（上一发还没收尾就再写 = 竞态，绝不允许）
        if should_abort and should_abort():
            return False, "急停已触发，本次点动未下发", detail
        ro, err = self.read_regs(ADDR_RO_TRIG, RO_WINDOW)
        if ro is None:
            return False, "读触发位失败: %s" % err, detail
        if ro[0] & 0x0001:
            return False, "点动触发位仍为 1（上一发未收尾），已拒绝本次下发", detail

        # 1) 写目标角 + 回读校验（"角度先落位、后触发"的硬顺序）
        ok, err = self.rc_write_jog_target(joints)
        if not ok:
            return False, err, detail
        ev("target", "目标角已写入并回读一致")

        # 2) settle ≥0.15s：写完立刻触发，程序可能读到上一发目标
        #    （症状：零位移但完成位瞬间回 1）—— 实测教训，必须等。
        time.sleep(0.15)
        if should_abort and should_abort():
            # 目标角已写但未触发 —— 不撤不补，触发位本来就是 0，直接放弃
            return False, "急停已触发，已放弃置触发位（目标角未执行）", detail

        # 3) 触发
        _, err = self.write_reg(ADDR_RO_TRIG, 0x0001)
        if err:
            return False, "置触发位失败: %s" % err, detail
        ev("trigger", "触发位已置 1")

        # 4) 等完成位（40035.Bit0）。超时按速度与位移估算：40103=5% ≈1.3°/s，
        #    100% ≈60~90°/s —— 用保守的 45°/s@100% 线性折算再放宽 2s。
        max_deg = max(abs(float(j)) for j in joints[:6])
        timeout = max(4.0, max_deg / max(1.0, 45.0 * min(100, speed_pct) / 100.0) * 2.0 + 2.0)
        t0 = time.time()
        done = False
        aborted = False
        while time.time() - t0 < timeout:
            if should_abort and should_abort():
                aborted = True
                break
            wo, err = self.read_regs(ADDR_WO_STAT, 1)
            if wo is not None and (wo[0] & 0x0001):
                done = True
                break
            time.sleep(0.05)
        # 5) 无论成败都撤触发（finally 语义：程序回到 WAIT 挂起）
        _, cerr = self.write_reg(ADDR_RO_TRIG, 0x0000)
        detail["elapsed_s"] = round(time.time() - t0, 3)
        detail["timeout_s"] = round(timeout, 2)
        if aborted:
            return False, "点动执行中被急停中止，触发位已撤", detail
        if not done:
            return False, ("点动完成位 %0.1fs 内未置位（检查点动服务程序是否在 WAIT 挂起）"
                           % timeout), detail
        if cerr:
            return False, "完成但撤触发失败: %s" % cerr, detail
        ev("done", "完成位已置 1，用时 %0.2fs" % detail["elapsed_s"])
        return True, None, detail

    def rc_estop(self, on: bool) -> Tuple[bool, Optional[str]]:
        """软件急停/停止：0x1005 停止命令字（旧 estop_addr 方案已证伪废弃）。"""
        if on:
            return self.rc_command(CMD_STOP)
        return True, None   # 复位不做物理动作（真机复位走控制器按钮/清报警流程）
