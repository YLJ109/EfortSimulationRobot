"""本地真空吸放脚本（不移动机器人）。

★ 原理：真空电磁阀接在控制器 io.DOut[8](吸)/[9](吹气) 上，Modbus 未暴露 DOut 直写，
  唯一通道 = 控制器常驻服务程序的触发位。控制器上已有 2001（吸+放+点动三合一服务，
  IDE 下发）：①等 40135.Bit1 → 吸；②等 Bit2 → 放；③等 Bit0 → 点动（本脚本绝不触发）。

流程：加载 2001 → 运行（程序挂起等触发）→ 置 40135.Bit1（吸，等 40035.Bit1 完成位）
      → 撤触发 → 置 Bit2（放，等 40035.Bit2）→ 撤触发。
全程不写任何运动指令、不触发 Bit0 → 机器人关节不会动。

用法：
  python tools/vacuum_local.py            # 默认 cycle：吸一次 + 放一次
  python tools/vacuum_local.py suck       # 只吸气
  python tools/vacuum_local.py release    # 只放气
"""
import os
import sys
import time

os.environ.setdefault("EFORT_REAL_MOTION", "1")   # 真实下发双闸之一（robot.yaml real_write 已 true）

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from app.services.modbus import (  # noqa: E402
    ADDR_RO_TRIG, ADDR_SET_PROG, ADDR_WO_STAT, CMD_LOAD, CMD_RUN,
    CMD_SERVO, CMD_STOP, CMD_ZERO,
    RO_WINDOW, VAC_BIT_RELEASE, VAC_BIT_SUCK,
)
from app.services.motion import motion  # noqa: E402

VAC_PROG = 200           # 三合一常驻服务程序（2026-09-29 起点动+吸+停吸合并进 200）
LOAD_WAIT_S = 2.5
TRIG_WAIT_S = 6.0


def snap():
    s, err = motion.modbus.rc_snapshot()
    return s, err


def ensure_servo() -> str:
    """伺服未吸合则拉起（官方 40101.Bit0=上/下伺服脉冲；CMD_ZERO→0.6s→CMD_SERVO）。

    ★ 上伺服只是上电/抱闸，**不含任何运动指令**，机器人不会动。
      伺服掉电时控制器忽略 CMD_LOAD/CMD_RUN（实测），故必须先吸合。
    返回 None=成功，否则错误消息。
    """
    s, _ = snap()
    if s is None:
        return "读快照失败，无法确认伺服状态"
    if s["bits"].get("servo"):
        print("  ✓ 伺服已吸合")
        return None
    print("  伺服未吸合，正在上伺服（CMD_ZERO → 0.6s → CMD_SERVO）...")
    _, err = motion.modbus.rc_command(CMD_ZERO)
    if err:
        return "CMD_ZERO 失败: %s" % err
    time.sleep(0.6)
    _, err = motion.modbus.rc_command(CMD_SERVO)
    if err:
        return "CMD_SERVO 失败: %s" % err
    t0 = time.time()
    while time.time() - t0 < 5.0:
        s, _ = snap()
        if s and s["bits"].get("servo"):
            print("  ✓ 伺服已吸合（用时 %0.1fs）" % (time.time() - t0))
            return None
        time.sleep(0.2)
    return "伺服 5s 内未吸合（检查急停/安全回路/示教器使能）"


def load_and_run(prog_no: int) -> str:
    """停止当前程序 → 加载 → 运行。返回 None=成功，否则错误消息。

    ★ 官方手册：程序运行过程中不可加载 —— 必须先 CMD_STOP 停掉在跑的程序
      （否则 CMD_LOAD 被控制器静默忽略，prog 纹丝不动）。
    ★ 加载成功的判据 = 40006(prog) 变成目标程序号，不能只看 prog_loaded 位
      （该位是残留的，上一发加载过就恒为 1，会误判）。
    """
    mb = motion.modbus
    # 0) 停掉当前在跑的程序（程序停止，非急停，不产生运动）
    _, err = mb.rc_command(CMD_STOP)                   # 0x1005
    if err:
        return "CMD_STOP 失败: %s" % err
    t0 = time.time()
    while time.time() - t0 < 2.0:
        s, _ = snap()
        if s and not s["bits"].get("run"):
            break
        time.sleep(0.1)
    # 1) 写目标程序号 + 加载
    _, err = mb.write_reg(ADDR_SET_PROG, prog_no)      # 40104 目标程序号
    if err:
        return "写 40104 失败: %s" % err
    _, err = mb.rc_command(CMD_LOAD)                   # 0x1011 加载
    if err:
        return "CMD_LOAD 失败: %s" % err
    t0 = time.time()
    loaded = False
    while time.time() - t0 < LOAD_WAIT_S:
        s, e = snap()
        if s and s["prog"] == prog_no and s["bits"].get("prog_loaded"):
            loaded = True
            break
        time.sleep(0.1)
    if not loaded:
        s, _ = snap()
        a = s.get("alarm1") if s else None
        return ("程序 %d 加载失败（prog 仍为 %s%s）——5005=程序不存在，"
                "或示教器停在文件/编辑界面（退出后重试）"
                % (prog_no, s.get("prog") if s else "?",
                   "，alarm1=%s" % a if a else ""))
    # 2) 运行（程序挂起等触发，不会动）
    _, err = mb.rc_command(CMD_RUN)                    # 0x1013
    if err:
        return "CMD_RUN 失败: %s" % err
    t0 = time.time()
    while time.time() - t0 < 2.0:
        s, _ = snap()
        if s and s["bits"].get("run"):
            return None
        time.sleep(0.1)
    return "CMD_RUN 后运行位 2s 未置位（程序可能立刻退出了）"


def trigger(action: str) -> str:
    """触发吸/放并等完成位。返回 None=成功，否则错误消息。"""
    mb = motion.modbus
    trig, done_bit, label = ((VAC_BIT_SUCK, 0x0002, "吸") if action == "suck"
                             else (VAC_BIT_RELEASE, 0x0004, "放"))
    ro, err = mb.read_regs(ADDR_RO_TRIG, RO_WINDOW)
    if ro is None:
        return "读触发位失败: %s" % err
    if ro[0] & trig:
        return "%s触发位仍为 1（上一发未收尾），拒绝" % label
    if ro[0] & 0x0001:
        return "点动触发位(Bit0)为 1，点动进行中——为安全拒绝本次吸放"
    _, err = mb.write_reg(ADDR_RO_TRIG, trig)
    if err:
        return "置%s触发位失败: %s" % (label, err)
    t0 = time.time()
    done = False
    cerr = None
    try:
        while time.time() - t0 < TRIG_WAIT_S:
            wo, e2 = mb.read_regs(ADDR_WO_STAT, 1)
            if wo is not None and (wo[0] & done_bit):
                done = True
                break
            time.sleep(0.05)
    finally:
        _, cerr = mb.write_reg(ADDR_RO_TRIG, 0x0000)   # 无论成败都撤触发
    el = round(time.time() - t0, 2)
    if not done:
        return "%s完成位 %0.1fs 未置位（2001 是否在运行？）" % (label, TRIG_WAIT_S)
    if cerr:
        return "完成但撤触发失败: %s" % cerr
    print("  ✓ %s气完成（io.DOut[8]=%s），用时 %0.2fs"
          % (label, "true" if action == "suck" else "false", el))
    return None


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "cycle").lower()
    if action not in ("suck", "release", "cycle"):
        print("用法: python tools/vacuum_local.py [suck|release|cycle]")
        return 2

    s, err = snap()
    if s is None:
        print("✗ 读控制器失败: %s" % err)
        return 1
    print("控制器: mode=%s prog=%d alarm1=%d alarm2=%d"
          % (s["mode"], s["prog"], s["alarm1"], s["alarm2"]))
    if s["alarm1"] or s["alarm2"]:
        print("✗ 控制器有报警，先清报警再测")
        return 1

    # 0) 伺服：掉电时 CMD_LOAD/CMD_RUN 会被控制器忽略（实测），必须先吸合
    e = ensure_servo()
    if e:
        print("✗ %s" % e)
        return 1

    if s["prog"] != VAC_PROG or not s["bits"].get("run"):
        print("加载并运行 %d（当前 prog=%d）..." % (VAC_PROG, s["prog"]))
        e = load_and_run(VAC_PROG)
        if e:
            print("✗ %s" % e)
            return 1
        print("  ✓ %d 已加载并运行（挂起等触发）" % VAC_PROG)
    else:
        print("✓ %d 已在运行" % VAC_PROG)

    rc = 0
    if action in ("suck", "cycle"):
        print("=== 吸气（40135.Bit1）===")
        e = trigger("suck")
        if e:
            print("  ✗ %s" % e)
            rc = 1
    if action in ("release", "cycle"):
        print("=== 放气（40135.Bit2）===")
        e = trigger("release")
        if e:
            print("  ✗ %s" % e)
            rc = 1

    s, _ = snap()
    if s:
        print("结束后: prog=%d mode=%s" % (s["prog"], s["mode"]))
    print("提示：本脚本运行期间 200 常驻（含点动+吸放），Web 点动可正常使用。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
