# -*- coding: utf-8 -*-
"""只读寄存器探测：确认速度设定/程序号/触发位/完成位的真实状态。★ 只读，不写任何寄存器。"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.services.modbus import (  # noqa: E402
    ADDR_PROG, ADDR_RO_TRIG, ADDR_SET_PROG, ADDR_SET_SPEED, ADDR_SPEED,
    ADDR_WO_STAT, ModbusRobot,
)


def main() -> int:
    mb = ModbusRobot()
    print("host=%s port=%s uid=%s" % (mb.host, mb.port, mb.uid))
    print("reachable=%s" % mb.reachable())

    snap, err = mb.rc_snapshot()
    if snap is None:
        print("快照失败: %s" % err)
        return 2

    print("\n--- 系统区 ---")
    print("status_word = 0x%04X" % snap["status_word"])
    for k, v in snap["bits"].items():
        print("  %-12s = %d" % (k, v))
    print("mode        = %s" % snap["mode"])
    print("prog(40006) = %d" % snap["prog"])
    print("alarm       = %s / %s" % (snap["alarm1"], snap["alarm2"]))
    print("joints      = %s" % ["%.3f" % v for v in snap["joints"]])

    print("\n--- 速度相关 ---")
    regs, e1 = mb.read_regs(ADDR_SPEED, 1)
    print("40003 运行速度%%        (addr %d) = %s  %s" % (ADDR_SPEED, regs, e1 or ""))
    regs, e2 = mb.read_regs(ADDR_SET_SPEED, 1)
    print("40103 速度设定%%        (addr %d) = %s  %s" % (ADDR_SET_SPEED, regs, e2 or ""))
    regs, e3 = mb.read_regs(ADDR_SET_PROG, 1)
    print("40104 目标程序号       (addr %d) = %s  %s" % (ADDR_SET_PROG, regs, e3 or ""))

    print("\n--- 点动用户区 40135~40144 ---")
    ro, e4 = mb.read_regs(ADDR_RO_TRIG, 10)
    if ro:
        print("40135 位区 word = 0x%04X" % ro[0])
        for i, nm in enumerate(("点动触发Bit0", "吸气触发Bit1", "放气触发Bit2")):
            print("  %-14s = %d" % (nm, (ro[0] >> i) & 1))
        print("40139~44 目标角×100 = %s" % ro[4:10])
        print("  解码(deg) = %s" % ["%.2f" % ModbusRobot.decode_angle(w) for w in ro[4:10]])
    else:
        print("读失败: %s" % e4)

    print("\n--- 完成位 40035 ---")
    wo, e5 = mb.read_regs(ADDR_WO_STAT, 1)
    if wo:
        print("40035 word = 0x%04X" % wo[0])
        for i, nm in enumerate(("点动完成Bit0", "吸气完成Bit1", "放气完成Bit2")):
            print("  %-14s = %d" % (nm, (wo[0] >> i) & 1))
    else:
        print("读失败: %s" % e5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())