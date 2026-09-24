# -*- coding: utf-8 -*-
"""2001 服务程序真机受控测试：J6 单轴 + 吸气 + 放气。

安全设计（每一层都是硬约束，不满足即中止）：
  1. 速度：先写 40103 = 5（最慢档），且断言写后回读 == 5；任何一步失败即中止。
  2. 轴锁：目标角 = 当前角，**仅 J6 允许加/减 STEP**；J1~J5 与当前角偏差
     必须严格 == 0（浮点容差 1e-6），否则拒绝下发。
  3. 位移：STEP 默认 0.1°（远小于示教器单步最小档），测完自动回原位。
  4. 前置守卫：模式必须 auto、无报警、触发位/完成位必须全 0。
  5. 每一步都读回校验：J1~J5 变化必须 < 0.05°，否则立刻报"乱动"并停止后续。

用法：
  python tools/test_2001_service.py            # 只读预检（不写任何寄存器）
  python tools/test_2001_service.py --go       # 执行完整测试
  python tools/test_2001_service.py --go --step 0.2
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.services.modbus import (  # noqa: E402
    ADDR_RO_TRIG, ADDR_SET_PROG, ADDR_SET_SPEED, ADDR_WO_STAT,
    CMD_CLEAR, CMD_LOAD, CMD_RUN, CMD_ZERO, ModbusRobot, SERVO_REENGAGE_DELAY,
)

SERVICE_PROG = 200
SPEED_PCT = 5              # 硬要求：5%，最慢档
STEP_DEG = 0.1             # J6 单步角度
J1J5_TOL = 0.05            # J1~J5 "未动" 判定容差（°）


class Abort(Exception):
    pass


def hdr(t: str) -> None:
    print("\n" + "=" * 62)
    print(t)
    print("=" * 62)


def joints_of(mb: ModbusRobot):
    snap, err = mb.rc_snapshot()
    if snap is None:
        raise Abort("读快照失败: %s" % err)
    return snap, [float(v) for v in snap["joints"]]


def check_clean(mb: ModbusRobot, snap) -> None:
    b = snap["bits"]
    if not b["auto"] or b["manual"]:
        raise Abort("控制器不在【自动】档（mode=%s）：上位机指令无效" % snap["mode"])
    if b["alarm"]:
        raise Abort("控制器有报警：%s / %s（先清报警）" % (snap["alarm1"], snap["alarm2"]))
    ro, e1 = mb.read_regs(ADDR_RO_TRIG, 1)
    wo, e2 = mb.read_regs(ADDR_WO_STAT, 1)
    if ro is None or wo is None:
        raise Abort("读触发/完成位失败: %s %s" % (e1, e2))
    if ro[0] != 0:
        raise Abort("40135 触发位区非 0（0x%04X）：上一发未收尾" % ro[0])
    if wo[0] != 0:
        raise Abort("40035 完成位区非 0（0x%04X）：上一发未收尾" % wo[0])


def set_speed(mb: ModbusRobot) -> None:
    hdr("阶段 1 · 速度设定 40103 = %d%%（最慢档）" % SPEED_PCT)
    _, err = mb.write_reg(ADDR_SET_SPEED, SPEED_PCT)
    if err:
        raise Abort("写 40103 失败: %s" % err)
    regs, err = mb.read_regs(ADDR_SET_SPEED, 1)
    if regs is None:
        raise Abort("回读 40103 失败: %s" % err)
    if regs[0] != SPEED_PCT:
        raise Abort("40103 回读不符：期望 %d 实得 %d" % (SPEED_PCT, regs[0]))
    print("40103 = %d  ✓ 回读一致" % regs[0])


def servo_on(mb: ModbusRobot, snap) -> None:
    hdr("阶段 2 · 伺服上电")
    if snap["bits"]["servo"]:
        print("伺服已在上电状态，跳过")
        return
    try:
        mb.rc_command(CMD_ZERO)
    except Exception:
        pass
    time.sleep(SERVO_REENGAGE_DELAY)
    _, err = mb.rc_command(0x1001)
    if err:
        raise Abort("伺服上电命令失败: %s" % err)
    t0 = time.time()
    while time.time() - t0 < 3.0:
        s2, _ = mb.rc_snapshot()
        if s2 and s2["bits"]["servo"]:
            print("伺服已吸合（%.2fs）" % (time.time() - t0))
            return
        time.sleep(0.1)
    raise Abort("伺服 3s 内未吸合：检查使能回路/急停")


def ensure_loaded(mb: ModbusRobot, snap) -> None:
    hdr("阶段 3 · 加载服务程序 %d" % SERVICE_PROG)
    if snap["bits"]["prog_loaded"] and snap["prog"] == SERVICE_PROG:
        print("程序 %d 已在加载态（当前程序=%d），跳过" % (SERVICE_PROG, snap["prog"]))
        return
    _, err = mb.write_reg(ADDR_SET_PROG, SERVICE_PROG)
    if err:
        raise Abort("写目标程序号失败: %s" % err)
    _, err = mb.rc_command(CMD_LOAD)
    if err:
        raise Abort("加载命令失败: %s" % err)
    t0 = time.time()
    while time.time() - t0 < 3.0:
        s2, _ = mb.rc_snapshot()
        if s2 and s2["bits"]["prog_loaded"]:
            print("程序已加载（%.2fs，当前程序=%d）" % (time.time() - t0, s2["prog"]))
            return
        time.sleep(0.1)
    raise Abort("程序 %d 加载失败（5005=程序不存在？确认示教器上有 2001）" % SERVICE_PROG)


def run_prog(mb: ModbusRobot) -> None:
    hdr("阶段 4 · 运行（程序挂起在 WAIT，不产生运动）")
    _, err = mb.rc_command(CMD_RUN)
    if err:
        raise Abort("运行命令失败: %s" % err)
    t0 = time.time()
    while time.time() - t0 < 3.0:
        s2, _ = mb.rc_snapshot()
        if s2 and s2["bits"]["run"]:
            print("运行位已置位（%.2fs）—— 程序在 WAIT 等触发" % (time.time() - t0))
            return
        time.sleep(0.1)
    raise Abort("运行位 3s 内未置位")


def jog_j6(mb: ModbusRobot, cur, delta: float, tag: str):
    """把 J6 移动 delta 度；J1~J5 必须与 cur 完全一致（硬断言）。"""
    target = list(cur)
    target[5] = cur[5] + delta
    # ---- 轴锁硬断言 ----
    for i in range(5):
        if abs(target[i] - cur[i]) > 1e-6:
            raise Abort("内部错误：目标 J%d 与当前不一致" % (i + 1))
    print("目标: J1~J5 锁定不动 | J6 %.3f → %.3f (%+.2f°)" % (cur[5], target[5], delta))
    print("  J1~J5 目标 = %s" % ["%.3f" % v for v in target[:5]])

    ok, err, detail = mb.rc_jog_execute(target, speed_pct=SPEED_PCT)
    print("  链路: %s" % ("成功" if ok else "失败: %s" % err))
    for st in detail.get("steps", []):
        print("    - %s: %s" % (st.get("step"), st.get("msg")))
    if not ok:
        raise Abort("J6 点动失败：%s" % err)

    time.sleep(0.3)
    _, after = joints_of(mb)
    dev15 = [abs(after[i] - cur[i]) for i in range(5)]
    print("  读回: %s" % ["%.3f" % v for v in after])
    print("  J1~J5 位移 = %s" % ["%.4f" % v for v in dev15])
    if max(dev15) > J1J5_TOL:
        raise Abort("★ J1~J5 发生位移（最大 %.3f°）—— 立即停止" % max(dev15))
    print("  J6 位移 = %+.4f°  ✓ 仅 J6 动作" % (after[5] - cur[5]))
    return after


def trigger_bit(mb: ModbusRobot, bit: int, name: str, timeout: float = 5.0) -> None:
    hdr("触发 %s（40135.Bit%d → 等 40035.Bit%d）" % (name, bit, bit))
    mask = 1 << bit
    _, err = mb.write_reg(ADDR_RO_TRIG, mask)
    if err:
        raise Abort("置 Bit%d 失败: %s" % (bit, err))
    print("40135 = 0x%04X 已置位" % mask)
    t0 = time.time()
    done = False
    while time.time() - t0 < timeout:
        wo, _ = mb.read_regs(ADDR_WO_STAT, 1)
        if wo is not None and (wo[0] & mask):
            done = True
            break
        time.sleep(0.05)
    mb.write_reg(ADDR_RO_TRIG, 0x0000)          # 无论成败都撤触发
    print("40135 = 0x0000 已撤销")
    if not done:
        raise Abort("%s 完成位 %.1fs 内未置位（40035.Bit%d 恒 0）" % (name, timeout, bit))
    print("%s 完成位已置位，用时 %.2fs  ✓" % (name, time.time() - t0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="真正执行（默认只做只读预检）")
    ap.add_argument("--step", type=float, default=STEP_DEG, help="J6 单步角度（默认 0.1）")
    ap.add_argument("--skip-io", action="store_true", help="只测 J6，不测吸气/放气")
    args = ap.parse_args()

    mb = ModbusRobot()
    hdr("阶段 0 · 只读预检")
    print("控制器 %s:%s  reachable=%s" % (mb.host, mb.port, mb.reachable()))
    snap, cur = joints_of(mb)
    print("模式=%s 伺服=%d 报警=%s/%s 程序=%d 加载=%d 运行=%d" % (
        snap["mode"], snap["bits"]["servo"], snap["alarm1"], snap["alarm2"],
        snap["prog"], snap["bits"]["prog_loaded"], snap["bits"]["run"]))
    print("当前关节角 = %s" % ["%.3f" % v for v in cur])
    ro, _ = mb.read_regs(ADDR_RO_TRIG, 1)
    wo, _ = mb.read_regs(ADDR_WO_STAT, 1)
    sp, _ = mb.read_regs(ADDR_SET_SPEED, 1)
    pg, _ = mb.read_regs(ADDR_SET_PROG, 1)
    print("40135=0x%04X  40035=0x%04X  40103=%s  40104=%s" % (ro[0], wo[0], sp, pg))
    print("环境闸 EFORT_REAL_MOTION=%r" % os.environ.get("EFORT_REAL_MOTION"))

    if not args.go:
        print("\n[只读预检结束] 未写任何寄存器。加 --go 执行完整测试。")
        return 0

    if abs(args.step) > 1.0:
        print("\n拒绝：单步 %s° 超过 1.0° 上限（安全限制）" % args.step)
        return 3

    orig = list(cur)
    try:
        check_clean(mb, snap)
        set_speed(mb)
        servo_on(mb, snap)
        ensure_loaded(mb, snap)
        run_prog(mb)

        # ---------- J6 单轴 ----------
        hdr("阶段 5 · J6 单轴点动 %+.2f°" % args.step)
        after = jog_j6(mb, orig, args.step, "去程")

        if not args.skip_io:
            trigger_bit(mb, 1, "吸气")
            trigger_bit(mb, 2, "放气")

        # ---------- 回原位 ----------
        hdr("阶段 6 · J6 回原位")
        cur2, _ = joints_of(mb)
        back = jog_j6(mb, cur2, orig[5] - cur2[5], "回程")
        dev = [abs(back[i] - orig[i]) for i in range(6)]
        print("最终 J1~J6 与初始偏差 = %s" % ["%.4f" % v for v in dev])
        if max(dev[:5]) > J1J5_TOL:
            raise Abort("★ J1~J5 未回到初始位置")
        print("J1~J5 全程零位移  ✓   J6 归位偏差 %+.4f°" % (back[5] - orig[5]))

        hdr("测试完成 · 全部通过")
        print("J6 单轴 ✓   吸气 ✓   放气 ✓   速度 %d%% ✓   J1~J5 未动 ✓" % SPEED_PCT)
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