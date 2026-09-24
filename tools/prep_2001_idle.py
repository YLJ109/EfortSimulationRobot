# -*- coding: utf-8 -*-
"""把 2001 点动通道复位到"空闲态"——拆雷脚本。

背景（2026-09-24 实测）：
  控制器上 40135.Bit0（点动触发位）可能残留为 1，而 40139~44（目标绝对角）
  还留着上一发的目标。此时若 2001 被启动，POU 里的 `WAIT ro_b[0]` 会立刻通过，
  直接执行 MJOINT 把机器人拖向那个残留目标角 —— 现场实测残留 J6=118° 而
  当前 J6=105°，一跑就是 13° 的无指令位移。

本脚本做两件事（都是零运动）：
  1) 写 40135 = 0x0000，撤掉所有残留触发位（Bit0 点动 / Bit1 吸气 / Bit2 放气）。
  2) 把 40139~44 写成**当前关节角**（回读校验）——双保险：万一 Bit0 再被置位，
     MJOINT 的目标就是当前位置，位移 = 0。

只写这两类寄存器，不写指令字、不上伺服、不碰速度设定。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.services.modbus import (  # noqa: E402
    ADDR_RO_TRIG, ADDR_WO_STAT, ModbusRobot,
)


def main() -> int:
    mb = ModbusRobot()
    print("控制器 %s:%s  reachable=%s" % (mb.host, mb.port, mb.reachable()))

    snap, err = mb.rc_snapshot()
    if snap is None:
        print("读快照失败: %s" % err)
        return 2

    cur = [float(v) for v in snap["joints"]]
    ro0, _ = mb.read_regs(ADDR_RO_TRIG, 10)
    wo0, _ = mb.read_regs(ADDR_WO_STAT, 1)
    if ro0 is None or wo0 is None:
        print("读点动用户区失败")
        return 2

    print("\n--- BEFORE ---")
    print("40135 = 0x%04X   40035 = 0x%04X" % (ro0[0], wo0[0]))
    print("  点动触发Bit0 = %d   吸气触发Bit1 = %d   放气触发Bit2 = %d"
          % (ro0[0] & 1, (ro0[0] >> 1) & 1, (ro0[0] >> 2) & 1))
    print("目标角 40139~44 = %s"
          % ["%.2f" % ModbusRobot.decode_angle(w) for w in ro0[4:10]])
    print("当前关节角      = %s" % ["%.3f" % v for v in cur])
    print("模式=%s 伺服=%d 运行=%d 程序=%d"
          % (snap["mode"], snap["bits"]["servo"], snap["bits"]["run"], snap["prog"]))
    if snap["bits"]["run"]:
        print("\n★ 拒绝：程序仍在运行态（run=1）。请先在示教器停止程序再复位。")
        return 3

    print("\n--- STEP 1 · 撤掉残留触发位（40135 = 0x0000）---")
    echo, e1 = mb.write_reg(ADDR_RO_TRIG, 0x0000)
    if e1:
        print("写 40135 失败: %s" % e1)
        return 2
    time.sleep(0.25)
    ro1, _ = mb.read_regs(ADDR_RO_TRIG, 10)
    print("回读 40135 = 0x%04X  %s" % (ro1[0], "✓ 已空闲" if ro1[0] == 0 else "✗ 仍非 0"))

    print("\n--- STEP 2 · 目标角对齐当前位姿（40139~44）---")
    ok, e2 = mb.rc_write_jog_target(cur)
    if not ok:
        print("写目标角失败: %s" % e2)
        return 2
    ro2, _ = mb.read_regs(ADDR_RO_TRIG, 10)
    dev = [abs(ModbusRobot.decode_angle(ro2[4 + i]) - cur[i]) for i in range(6)]
    print("回读目标角 = %s"
          % ["%.2f" % ModbusRobot.decode_angle(w) for w in ro2[4:10]])
    print("与当前偏差 = %s" % ["%.3f" % v for v in dev])
    print("final 40135 = 0x%04X" % ro2[0])

    if ro2[0] != 0:
        print("\n★ 40135 仍非 0，请勿启动程序。")
        return 2
    if max(dev) > 0.02:
        print("\n★ 目标角与当前位姿偏差过大（>0.02°），请勿启动程序。")
        return 2

    print("\n[OK] 点动通道已复位到空闲态：触发位全 0，目标角 = 当前位姿（位移恒为 0）。")
    print("     现在启动 2001 也不会产生任何无指令运动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())