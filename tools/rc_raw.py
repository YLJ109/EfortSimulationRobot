# -*- coding: utf-8 -*-
"""控制器原始命令工具（非运动指令，绝不产生位移）。

用法：
    python tools/rc_raw.py status     # 只读状态字/报警/程序号
    python tools/rc_raw.py stop       # CMD_STOP  (0x1005)  停程序（不等同急停，不断伺服）
    python tools/rc_raw.py clear      # CMD_CLEAR (0x1009)  清报警
    python tools/rc_raw.py reset      # stop → 等 1s → clear → 等 1s → status（顺序修复验证）

★ 为什么需要它：后端没有暴露"停程序/清报警"的独立接口，而 ready() 是
  「报警→伺服→加载→运行」的顺序：**在程序仍在运行时清报警，坏程序会立刻把
  5005 重新顶上来**（实测清了 0.5s 内就复现）。要验证/修复就得能先停机。
★ 用后端自己的 ModbusRobot 类，走同一份 config/robot.yaml。
★ 本工具只发**非运动**命令，不写目标角、不置触发位。
"""
import sys
import time

sys.path.insert(0, "backend")

from app.services.modbus import ModbusRobot  # noqa: E402

CMD_STOP = 0x1005
CMD_CLEAR = 0x1009


def show(mb, tag):
    s, err = mb.rc_snapshot()
    if s is None:
        print("[%s] 读快照失败: %s" % (tag, err))
        return None
    bits = {k: v for k, v in (s.get("bits") or {}).items() if v}
    print("[%s] 状态字=%s bits=%s prog=%s alarm=%s/%s joints=%s" % (
        tag, s.get("status_word"), bits, s.get("prog"),
        s.get("alarm1"), s.get("alarm2"),
        [round(float(x), 3) for x in (s.get("joints") or [])]))
    return s


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    mb = ModbusRobot()
    try:
        if cmd == "status":
            show(mb, "status")
        elif cmd == "stop":
            show(mb, "before")
            print("  -> CMD_STOP  返回 %s" % (mb.rc_command(CMD_STOP),))
            time.sleep(1.0)
            show(mb, "after ")
        elif cmd == "clear":
            show(mb, "before")
            print("  -> CMD_CLEAR 返回 %s" % (mb.rc_command(CMD_CLEAR),))
            time.sleep(1.0)
            show(mb, "after ")
        elif cmd == "reset":
            show(mb, "初始   ")
            r = mb.rc_command(CMD_STOP)
            print("  -> CMD_STOP  返回 %s（停程序，不断伺服、不产生位移）" % (r,))
            time.sleep(1.2)
            show(mb, "停机后 ")
            r = mb.rc_command(CMD_CLEAR)
            print("  -> CMD_CLEAR 返回 %s" % (r,))
            time.sleep(1.2)
            s = show(mb, "清完后 ")
            if s and not s["bits"].get("alarm"):
                print("\n★ 报警已清除且未复现 → 说明 5005 是**运行中的坏程序**持续顶上来的；")
                print("  正确顺序应是「先停机 → 再清报警」，ready() 的顺序需要改。")
            elif s:
                print("\n★ 清完仍报警（码 %s/%s）→ 报警源不在运行状态，"
                      "需查示教器：是否停在文件/编辑界面、或该程序本身损坏/不存在。"
                      % (s.get("alarm1"), s.get("alarm2")))
        else:
            print(__doc__)
            return 2
    finally:
        try:
            mb.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
