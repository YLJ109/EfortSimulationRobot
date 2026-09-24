# -*- coding: utf-8 -*-
"""控制器 Modbus 寄存器**只读**勘察工具。

★ 本工具只发 FC3（读保持寄存器），**绝不写入任何寄存器**。
  存在的意义：robot.yaml 里 motion.write_base_addr / write_speed_addr /
  estop_addr 至今标着 TODO（未按手册核对）。在手册到位之前，
  至少要把"控制器到底暴露了哪些地址、哪些地址在动"摸清楚 ——
  没有这些事实，开启 motion.real_write 就是在盲写真机。

用法：
    python scripts/reg_survey.py windows    # 探测允许读取的地址窗口
    python scripts/reg_survey.py dump       # 全量 dump 可读寄存器（0..400）
    python scripts/reg_survey.py joints     # 复核两路关节角读数
    python scripts/reg_survey.py watch [秒] # 旁路监听寄存器变化（操作示教器时用）

安全边界（不要越过）：
    · 只读。任何 FC6/FC16 都不在本工具范围内。
    · ★ 特别注意：**不要**试图用"写 0 试探"来验证目标寄存器区。
      如果某个地址区真的是"目标关节角"，写 0 = 命令机器人回到全零位姿，
      这比写当前位姿危险得多。写路径必须等手册核对后再开。
"""
from __future__ import annotations

import socket
import struct
import sys
import time

HOST = "192.168.1.12"
PORT = 502
UID = 1
MAXADDR = 400


def _w2f(low: int, high: int) -> float:
    """两寄存器 → float32（大端、字序交换），与 services/modbus.py 一致。"""
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


def _i16(v: int) -> int:
    return v - 65536 if v > 32767 else v


class Client:
    """复用单条 TCP 长连接。控制器对短连接连打比较敏感（会回异常）。"""

    def __init__(self, host: str = HOST, port: int = PORT, uid: int = UID) -> None:
        self.host, self.port, self.uid = host, port, uid
        self.tid = 0
        self.s: socket.socket | None = None
        self._connect()

    def _connect(self) -> None:
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.settimeout(2.5)
        self.s.connect((self.host, self.port))

    def _recv(self) -> bytes:
        buf = b""
        while len(buf) < 6:
            c = self.s.recv(6 - len(buf))
            if not c:
                raise ConnectionError("连接被关闭")
            buf += c
        total = 6 + struct.unpack(">H", buf[4:6])[0]
        while len(buf) < total:
            c = self.s.recv(total - len(buf))
            if not c:
                raise ConnectionError("响应不完整")
            buf += c
        return buf

    def read(self, addr: int, qty: int, retry: int = 3):
        """返回 (寄存器列表, None) 或 (None, 错误串)。"""
        last = "unknown"
        for _ in range(retry):
            try:
                self.tid = (self.tid + 1) & 0xFFFF
                pdu = struct.pack(">BHH", 3, addr, qty)
                frame = struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, self.uid) + pdu
                self.s.sendall(frame)
                data = self._recv()
                if len(data) < 9:
                    last = "响应过短"
                    raise ConnectionError(last)
                fc = data[7]
                if fc & 0x80:
                    return None, "Modbus 异常 0x%02x" % data[8]
                bc = data[8]
                return list(struct.unpack(">%dH" % (bc // 2), data[9:9 + bc])), None
            except Exception as e:  # noqa: BLE001
                last = str(e)
                try:
                    self.s.close()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.2)
                try:
                    self._connect()
                except Exception as e2:  # noqa: BLE001
                    last = str(e2)
        return None, last

    def close(self) -> None:
        try:
            self.s.close()
        except Exception:  # noqa: BLE001
            pass


def cmd_windows(c: Client) -> None:
    """哪种 (起始地址, 数量) 组合会被控制器接受 —— 决定读窗口。"""
    print("=" * 78)
    print("地址窗口探测：start 0..63 各读 12 个寄存器")
    print("=" * 78)
    ok = []
    for st in range(0, 64):
        regs, err = c.read(st, 12)
        print("  start=%-3d %s" % (st, regs if regs else err))
        if err is None:
            ok.append(st)
        time.sleep(0.1)
    print()
    print("允许 qty=12 的起始地址:", ok)
    print("★ 经验：单个寄存器读(qty=1)与整块读(qty=12)的**允许集合不同** ——")
    print("  例如 addr=10 qty=1 被拒，但 addr=10 qty=12 正常返回关节角。")
    print("  判断可读性必须用与业务一致的 qty，不能只按单寄存器探测。")


def cmd_dump(c: Client) -> None:
    print("=" * 78)
    print("全量只读 dump  addr 0..%d" % MAXADDR)
    print("=" * 78)
    vals, bad = {}, []
    for a in range(0, MAXADDR + 1):
        regs, err = c.read(a, 1)
        if err is None:
            vals[a] = regs[0]
        else:
            bad.append(a)
    print("可读 %d 个 / 不可读 %d 个" % (len(vals), len(bad)))
    print()
    print("--- 非零寄存器 ---")
    for a, v in sorted(vals.items()):
        if v:
            print("  addr=%-4d %-8d (0x%04X)" % (a, v, v))
    print()
    print("--- 每 12 个一行（*值 = 非零，X = 不可读，. = 零） ---")
    for base in range(0, MAXADDR + 1, 12):
        cells = []
        any_r = False
        for i in range(12):
            a = base + i
            if a in vals:
                any_r = True
                cells.append(("*%d" % vals[a]) if vals[a] else ".")
            else:
                cells.append("X")
        if any_r:
            print("  %-4d  %s" % (base, "  ".join(cells)))


def cmd_joints(c: Client) -> None:
    """控制器暴露了**两路**关节角读数，交叉核对。"""
    print("=" * 78)
    print("两路关节角读数交叉核对")
    print("=" * 78)
    fl, e1 = c.read(10, 12)
    if e1:
        print("  float 区 (addr 10..21) 读取失败:", e1)
    else:
        deg = [_w2f(fl[i * 2], fl[i * 2 + 1]) for i in range(6)]
        print("  float32 @10..21 : %s" % ", ".join("%.3f" % v for v in deg))
    it, e2 = c.read(138, 6)
    if e2:
        print("  int16  区 (addr 138..143) 读取失败:", e2)
    else:
        deg2 = [_i16(v) / 100.0 for v in it]
        print("  int16×100 @138..143 : %s" % ", ".join("%.3f" % v for v in deg2))
        print("  裸寄存器: %s" % it)
    if not e1 and not e2:
        d = max(abs(a - b) for a, b in zip(deg, deg2))
        print("  两路最大偏差: %.4f 度  → %s" % (d, "一致" if d < 0.02 else "不一致，需排查"))
    print()
    print("★ 这两路是**不同表示**的同一组关节角：")
    print("  · 采集器(services/collector.py)用的是 float32 那路 —— 不要动它。")
    print("  · int16×100 那路单位是 0.01°，可作为「是否读到真机」的第二证据。")


def cmd_watch(c: Client, dur: float = 120.0) -> None:
    """旁路监听：只报"哪个寄存器从什么变成了什么"。"""
    windows = [(0, 12), (34, 12), (100, 12), (128, 12), (132, 12),
               (300, 12), (312, 12), (324, 12), (336, 12), (348, 12), (360, 12)]

    def snap():
        m = {}
        for st, q in windows:
            r, _ = c.read(st, q)
            if r:
                for i, v in enumerate(r):
                    m[st + i] = v
        return m

    print("=" * 78)
    print("只读变化监听 %.0f 秒 —— 请在示教器上操作，本工具只记录变化" % dur)
    print("=" * 78)
    t0 = time.time()
    prev = snap()
    print("基线非零:", [(a, prev[a]) for a in sorted(prev) if prev[a]])
    while time.time() - t0 < dur:
        time.sleep(0.6)
        cur = snap()
        diff = [(a, prev.get(a), cur.get(a))
                for a in sorted(set(list(cur) + list(prev)))
                if prev.get(a) != cur.get(a)]
        if diff:
            print("[t=%7.2fs] %s" % (time.time() - t0,
                                     "  ".join("addr%d: %s->%s" % d for d in diff)))
        prev = cur
    print("监听结束。")


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "dump"
    c = Client()
    try:
        if what == "windows":
            cmd_windows(c)
        elif what == "dump":
            cmd_dump(c)
        elif what == "joints":
            cmd_joints(c)
        elif what == "watch":
            cmd_watch(c, float(sys.argv[2]) if len(sys.argv) > 2 else 120.0)
        else:
            print(__doc__)
    finally:
        c.close()


if __name__ == "__main__":
    main()
