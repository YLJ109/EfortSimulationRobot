# -*- coding: utf-8 -*-
"""扫描控制器上"存在哪些程序号" —— **只加载、绝不运行**（复用开源项目 efort_panel_pyqt6 的
现场实测判据）。

用法（backend 目录下）:
    python tools/scan_programs.py [起=1] [止=300]

判据（2026-09-21/23 现场实测，见参照项目 tools/probe_load.py 时间线）:
  · 存在   : 40001.Bit11 程序加载位置位 —— 实测 ≈0.50s 才拉高（真脉冲，必须等得够久）
  · 不存在 : 报警 5005 出现 —— 实测 ≈0.08s（快且锁存，比等脉冲可靠）
  · 40006  : 当前程序号不随加载变化，不能当判据

⚠ 历史坑：等待窗口若≤0.5s 会卡在临界漏判（真实加载要 0.50~0.61s），这里窗口 ≥1.2s，
  且"置位 / 报警 / 超时 谁先到就停"。

全程只写 40104（目标程序号）+ 40101（加载/清报警），**绝不写 Bit1/Bit2、不触发运动**。
结束后自动还原 40104 为初始程序号、清报警。
"""
from __future__ import annotations

import os
import sys
import time

# 允许直接 `python tools/scan_programs.py` 运行
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.motion import motion  # noqa: E402
from app.services.modbus import (  # noqa: E402
    CMD_CLEAR, CMD_LOAD, ADDR_SET_PROG, STATUS_BITS,
)

BUDGET = 1.4      # 单号等待窗口（s），≥1.2 避免临界漏判


def _light():
    """轻量读：一次拿 40001~40004（状态 + 报警1 + 报警2）。"""
    return motion.modbus.read_regs(0, 4)


def _probe(n: int, budget: float):
    """探一个号。返回 (kind, 秒, 说明)。只写程序号+加载，不动运动位。"""
    try:
        motion.modbus.write_reg(ADDR_SET_PROG, n)
        motion.modbus.rc_command(CMD_LOAD)
    except Exception as e:
        return ("error", 0.0, str(e)[:80])
    t0 = time.time()
    while True:
        dt = time.time() - t0
        w, err = _light()
        if w is not None:
            if (w[0] >> STATUS_BITS["prog_loaded"]) & 1:
                return ("exists", dt, "Bit11 置位")
            if w[2] or w[3]:          # 40004/40005 报警1/2
                return ("missing", dt, "报警 %d/%d" % (w[2], w[3]))
        if dt >= budget:
            return ("timeout", dt, "无置位也无报警")
        time.sleep(0.04)


def main() -> int:
    if not motion.real:
        print("[注意] real_write 未开启（模拟模式），扫描仍会对 Modbus 发加载命令。")
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    # 记初始程序号，结束还原
    try:
        initial = motion.modbus.read_regs(0, 6)
        init_prog = initial[5] if initial else 0
    except Exception:
        init_prog = 0

    print(f"扫描程序号区间 [{lo}, {hi}]（只加载不运行；窗口 {BUDGET}s/号）...")
    found = []
    for n in range(lo, hi + 1):
        kind, dt, note = _probe(n, BUDGET)
        if kind == "exists":
            found.append(n)
            print(f"  ✅ #{n:>4}  存在（{note}, {dt:.2f}s）")
        elif kind == "missing":
            # 只打印几种常见号附近的缺失，控制噪音
            if n in (200, 201, 410, 411, 412) or (n >= 400 and n <= 420):
                print(f"  ✗ #{n:>4}  缺失（{note}）")
        time.sleep(0.03)

    # 还原程序号 + 清报警
    try:
        if init_prog:
            motion.modbus.write_reg(ADDR_SET_PROG, init_prog)
        motion.modbus.rc_command(CMD_CLEAR)
    except Exception:
        pass

    print(f"\n结果：存在的前 {len(found)} 个程序号 = {found}")
    if found:
        print("→ 点动服务程序请用上面某个真实存在的号，改 config/robot.yaml 的 "
              "motion.jog.service_program。")
    else:
        print("→ 区间内未发现程序。点动服务程序需要先在示教器上建好（参见过滤模板）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())