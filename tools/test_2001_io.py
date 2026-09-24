# -*- coding: utf-8 -*-
"""2001 服务程序 —— **纯 IO** 受控测试（吸气 / 放气），全程零运动。

与 test_2001_service.py 的区别：那个会上伺服、会点动 J6；这个**绝不**碰运动通道。

安全门禁（任一条不满足即中止，不写任何触发位）：
  1. 模式必须 auto（manual 档下上位机指令无效）。
  2. 无报警。
  3. 40103 速度设定必须 **严格 == 5**（不是 5 就不测）。
  4. 40135 触发位区必须 == 0（无残留触发）。
  5. 40139~44 目标角必须 == 当前关节角（偏差 <0.02°）——保证"万一 Bit0 被置位，
     MJOINT 目标就是当前位置，位移恒为 0"。
  6. 程序 2001 必须已加载且**在运行态**（run=1），否则各服务不会被调用。
  7. 被触发的那一路，其完成位（40035.BitN）必须是 0（说明服务已武装、等待触发）。

执行顺序（只用 Bit1 / Bit2，**永不触碰 Bit0**）：
  ① 吸气：置 40135.Bit1 → 等 40035.Bit1 → 撤 Bit1 → 校验位移
  ② 放气：置 40135.Bit2 → 等 40035.Bit2 → 撤 Bit2 → 校验位移

每一步后都回读关节角：J1~J6 位移必须 < 0.05°，否则立刻报"乱动"并撤触发。

用法：
  python tools/test_2001_io.py              # 只读预检（不写任何寄存器）
  python tools/test_2001_io.py --start      # 预检 + 发 RUN 让 2001 进入运行态
  python tools/test_2001_io.py --go         # 预检 + 执行吸气/放气测试
  python tools/test_2001_io.py --go --start # 一条龙（预检 → 启动 → 测试）
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.services.modbus import (  # noqa: E402
    ADDR_RO_TRIG, ADDR_SET_SPEED, ADDR_WO_STAT, CMD_RUN, ModbusRobot,
)

SERVICE_PROG = 200
SPEED_PCT = 5          # 硬要求：严格 5%
J_TOL = 0.05           # 关节"未动"判定容差（°）
TARGET_TOL = 0.02      # 目标角与当前位姿的容许偏差（°）
BIT_SUCK = 1           # 吸气
BIT_BLOW = 2           # 放气
BIT_JOG = 0            # ★ 绝不使用


class Abort(Exception):
    pass


def hdr(t: str) -> None:
    print("\n" + "=" * 64)
    print(t)
    print("=" * 64)


def read_state(mb: ModbusRobot):
    snap, err = mb.rc_snapshot()
    if snap is None:
        raise Abort("读快照失败: %s" % err)
    ro, e1 = mb.read_regs(ADDR_RO_TRIG, 10)
    wo, e2 = mb.read_regs(ADDR_WO_STAT, 1)
    if ro is None or wo is None:
        raise Abort("读点动用户区失败: %s %s" % (e1, e2))
    return snap, ro, wo


def targets_of(ro) -> list:
    return [ModbusRobot.decode_angle(w) for w in ro[4:10]]


def preflight(mb: ModbusRobot, strict: bool = True):
    hdr("阶段 0 · 只读预检")
    snap, ro, wo = read_state(mb)
    cur = [float(v) for v in snap["joints"]]
    tgt = targets_of(ro)

    print("控制器 %s:%s  reachable=%s" % (mb.host, mb.port, mb.reachable()))
    print("模式=%s  伺服=%d  报警=%s/%s  程序=%d  加载=%d  运行=%d"
          % (snap["mode"], snap["bits"]["servo"], snap["alarm1"], snap["alarm2"],
             snap["prog"], snap["bits"]["prog_loaded"], snap["bits"]["run"]))
    print("当前关节角   = %s" % ["%.3f" % v for v in cur])
    print("目标角 40139~44 = %s" % ["%.2f" % v for v in tgt])
    print("40135 = 0x%04X  (点动%d 吸气%d 放气%d)   40035 = 0x%04X"
          % (ro[0], ro[0] & 1, (ro[0] >> 1) & 1, (ro[0] >> 2) & 1, wo[0]))

    sp, err = mb.read_regs(ADDR_SET_SPEED, 1)
    print("40103 速度设定 = %s" % sp)

    if not strict:
        return snap, cur, ro, wo

    # ---- 门禁 ----
    if snap["mode"] != "auto":
        raise Abort("控制器不在【自动】档（当前 %s）—— 上位机指令无效，请在示教器切回 AUTO"
                    % snap["mode"])
    if snap["bits"]["alarm"]:
        raise Abort("控制器有报警：%s / %s（先清报警）" % (snap["alarm1"], snap["alarm2"]))
    if sp is None or sp[0] != SPEED_PCT:
        raise Abort("★ 速度设定 40103 = %s，不是 %d —— 拒绝测试" % (sp, SPEED_PCT))
    if ro[0] != 0:
        raise Abort("★ 40135 有残留触发位（0x%04X）—— 先跑 tools/prep_2001_idle.py" % ro[0])
    dev = [abs(tgt[i] - cur[i]) for i in range(6)]
    if max(dev) > TARGET_TOL:
        raise Abort("★ 目标角与当前位姿偏差 %.3f°（>%.2f°）—— 先跑 tools/prep_2001_idle.py"
                    % (max(dev), TARGET_TOL))
    if snap["prog"] != SERVICE_PROG:
        raise Abort("当前程序 = %d，不是 %d" % (snap["prog"], SERVICE_PROG))
    if not snap["bits"]["prog_loaded"]:
        raise Abort("程序 %d 未加载" % SERVICE_PROG)
    print("\n[门禁] 模式/报警/速度(5%)/触发位/目标角/程序号 全部通过 ✓")
    return snap, cur, ro, wo


def start_program(mb: ModbusRobot, snap) -> None:
    hdr("阶段 1 · 启动程序 %d（使其挂起在 ① 等触发）" % SERVICE_PROG)
    if snap["bits"]["run"]:
        print("运行位已置位，跳过")
        return
    _, err = mb.rc_command(CMD_RUN)
    if err:
        raise Abort("发 RUN 命令失败: %s" % err)
    t0 = time.time()
    while time.time() - t0 < 3.0:
        s2, _ = mb.rc_snapshot()
        if s2 and s2["bits"]["run"]:
            print("运行位已置位（%.2fs）—— 程序在 ① 等 40135.Bit1" % (time.time() - t0))
            return
        time.sleep(0.1)
    raise Abort("运行位 3s 内未置位（控制器可能要求先上伺服；可在示教器手动启动）")


def check_no_motion(mb: ModbusRobot, cur, tag: str) -> list:
    time.sleep(0.25)
    snap, ro, wo = read_state(mb)
    after = [float(v) for v in snap["joints"]]
    dev = [abs(after[i] - cur[i]) for i in range(6)]
    print("  回读关节角 = %s" % ["%.3f" % v for v in after])
    print("  位移(deg)  = %s" % ["%.4f" % v for v in dev])
    if max(dev) > J_TOL:
        raise Abort("★ %s 期间发生位移（最大 %.3f°）—— 立即停止" % (tag, max(dev)))
    print("  ✓ 零位移（J1~J6 全部 < %.2f°）" % J_TOL)
    return after


def trigger(mb: ModbusRobot, bit: int, name: str, cur, timeout: float = 8.0):
    mask = 1 << bit
    hdr("触发 %s（40135.Bit%d → 等 40035.Bit%d）" % (name, bit, bit))

    snap, ro, wo = read_state(mb)
    if ro[0] & (1 << BIT_JOG):
        raise Abort("★ 40135.Bit0（点动触发）为 1 —— 立即中止")
    if ro[0] & mask:
        raise Abort("Bit%d 已是置位状态，拒绝重复触发" % bit)
    if wo[0] & mask:
        raise Abort("完成位 40035.Bit%d 已为 1（服务未武装）—— 程序可能不在 ①/② 等待点" % bit)

    _, err = mb.write_reg(ADDR_RO_TRIG, mask)
    if err:
        raise Abort("置 Bit%d 失败: %s" % (bit, err))
    print("40135 = 0x%04X 已置位" % mask)

    t0 = time.time()
    done = False
    while time.time() - t0 < timeout:
        wo2, _ = mb.read_regs(ADDR_WO_STAT, 1)
        if wo2 is not None and (wo2[0] & mask):
            done = True
            break
        time.sleep(0.05)
    elapsed = time.time() - t0

    _, cerr = mb.write_reg(ADDR_RO_TRIG, 0x0000)      # 无论成败都撤触发
    print("40135 = 0x0000 已撤销")
    if not done:
        raise Abort("%s 完成位 %.1fs 内未置位 —— 程序可能停在别的服务等待点（超时，无风险）"
                    % (name, timeout))
    if cerr:
        raise Abort("完成但撤触发失败: %s" % cerr)
    print("%s 完成位已置位，用时 %.2fs  ✓" % (name, elapsed))
    check_no_motion(mb, cur, name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="执行吸气/放气测试")
    ap.add_argument("--start", action="store_true", help="预检通过后发 RUN 启动 2001")
    args = ap.parse_args()

    mb = ModbusRobot()
    try:
        snap, cur, ro, wo = preflight(mb, strict=bool(args.go or args.start))

        if not (args.go or args.start):
            print("\n[只读预检结束] 未写任何寄存器。加 --go 执行测试，加 --start 启动程序。")
            return 0

        if args.start:
            start_program(mb, snap)

        if not args.go:
            print("\n[已完成启动] 未触发任何服务。")
            return 0

        snap, cur, ro, wo = read_state(mb)
        if not snap["bits"]["run"]:
            raise Abort("程序不在运行态（run=0）—— 服务不会被调用，请先 --start 或示教器启动")

        trigger(mb, BIT_SUCK, "吸气（io.DOut[8]=true）", cur)
        trigger(mb, BIT_BLOW, "放气（io.DOut[8]=false + DOut[9] 脉冲）", cur)

        hdr("测试完成 · 全部通过")
        print("吸气 ✓   放气 ✓   速度设定 40103=%d ✓   J1~J6 全程零位移 ✓" % SPEED_PCT)
        print("程序现停在 ③ 点动服务等待点（等 40135.Bit0）—— 未触发，机器人不会动。")
        return 0
    except Abort as e:
        hdr("中止")
        print("原因: %s" % e)
        try:
            mb.write_reg(ADDR_RO_TRIG, 0x0000)
            print("已撤 40135 触发位（复位到空闲）")
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())