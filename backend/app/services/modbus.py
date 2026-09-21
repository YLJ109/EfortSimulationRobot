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
import time
from typing import List, Optional, Tuple

from app.core.config import get_config
from app.core.logger import get_logger

log = get_logger("modbus")


def _build(func: int, addr: int, qty: int, uid: int = 1, tid: int = 1) -> bytes:
    pdu = struct.pack(">BHH", func, addr, qty)
    return struct.pack(">HHHB", tid, 0, len(pdu) + 1, uid) + pdu


def _words_to_float(low: int, high: int) -> float:
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


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
