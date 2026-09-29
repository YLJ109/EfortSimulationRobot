# -*- coding: utf-8 -*-
"""210 三合一全项验收：等 AUTO → 就绪 → 零位移探针 → 真实 J6 位移 → 吸气 → 停止吸气。

用法：
    python tools/rc_full_test.py            # 等 AUTO 最多 120s
    python tools/rc_full_test.py --wait 300 # 等 AUTO 最多 300s
    python tools/rc_full_test.py --no-move  # 不动机器人（跳过真实位移）

安全口径（现场既定）：
  · 控制器必须 AUTO（T1/T2 下 PC 指令被拒）
  · 只动 J6、单次 1.0°、5°/s 慢速；J1~J5 由轴锁强制拒绝
  · 零位移探针：目标 = 当前位姿，机器人不会动，仅用于判定 40135.Bit0 分支是否存在
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
PROG = 210
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, tok=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if tok:
        h["X-Control-Token"] = tok
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with _OP.open(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw.decode("utf-8", "replace")}
    except Exception as e:
        return 0, {"error": str(e)}


def st(tok):
    _, d = call("GET", "/api/rc-status", tok=tok)
    return d or {}


def joints(d):
    return [round(float(x), 3) for x in (d.get("joints") or [])]


def bits(d):
    return {k: v for k, v in (d.get("bits") or {}).items() if v}


def main():
    wait_s = 120
    for i, a in enumerate(sys.argv):
        if a == "--wait" and i + 1 < len(sys.argv):
            wait_s = int(sys.argv[i + 1])
    do_move = "--no-move" not in sys.argv

    _, d = call("POST", "/api/auth/login", {"password": "admin123"})
    tok = d["token"]

    print("等待控制器切到 AUTO（最多 %ds，每 4s 看一次）…" % wait_s)
    t0 = time.time()
    mode = None
    while time.time() - t0 < wait_s:
        _, rm = call("GET", "/api/control/run-mode", tok=tok)
        mode = rm.get("observed_mode") or rm.get("mode")
        if mode == "AUTO":
            print("  → 已到 AUTO（%.0fs）" % (time.time() - t0))
            break
        print("  当前 %s … 等" % mode)
        time.sleep(4)
    if mode != "AUTO":
        print("\n!! 仍未切到 AUTO，未下发任何指令。请在示教器把模式旋钮拨到 AUTO 后重跑。")
        return 2

    call("POST", "/api/control/run-mode", {"mode": "AUTO", "note": "210 验收"}, tok=tok)

    results = []

    # ---------- 1) 一键就绪：加载并运行 210 ----------
    s0 = st(tok)
    print("\n[1] 一键就绪 210")
    print("    前: prog=%s bits=%s alarm=%s/%s" % (
        s0.get("prog"), bits(s0), s0.get("alarm1"), s0.get("alarm2")))
    _, rr = call("POST", "/api/ready", {"prog": PROG}, tok=tok, timeout=120)
    steps = rr.get("steps") or []
    for s in steps:
        print("      %-9s ok=%-5s %s" % (s.get("step"), s.get("ok"), s.get("msg")))
    ok_ready = bool(rr.get("ok"))
    results.append(("一键就绪(加载运行 210)", ok_ready, rr.get("error") or ""))
    if not ok_ready:
        print("!! 就绪失败，后续项全部跳过：%s" % rr.get("error"))
        return 3
    s1 = st(tok)
    print("    后: prog=%s bits=%s alarm=%s/%s" % (
        s1.get("prog"), bits(s1), s1.get("alarm1"), s1.get("alarm2")))
    prog_ok = str(s1.get("prog")) == str(PROG)
    run_ok = bool((s1.get("bits") or {}).get("run"))
    results.append(("210 已就位且在运行", prog_ok and run_ok, ""))

    # ---------- 2) 零位移探针：判定 40135.Bit0 分支 ----------
    cur = joints(s1)
    print("\n[2] 零位移探针（目标=当前位姿，机器人不会动）")
    print("    当前位姿 %s" % cur)
    _, zp = call("POST", "/api/control/move", {"joints": cur, "speed_pct": 5}, tok=tok, timeout=90)
    zdet = zp.get("detail") or {}
    print("    ok=%s  用时=%ss" % (zp.get("ok"), zdet.get("elapsed_s")))
    if zp.get("error"):
        print("    error=%s" % zp["error"])
    results.append(("40135.Bit0 分支存在（零位移也置完成位）", bool(zp.get("ok")),
                    zp.get("error") or ""))
    if not zp.get("ok"):
        print("\n!! Bit0 分支未生效 —— 210 没真的下载/运行，或程序内容不对。")
    time.sleep(0.4)

    # ---------- 3) 真实位移：J6 +1.0° ----------
    if do_move:
        j0 = joints(st(tok))
        print("\n[3] 真实位移 J6 +1.0° @5°/s")
        print("    前 J6=%.3f" % j0[5])
        _, mv = call("POST", "/api/control/jog/step",
                     {"joint": 6, "dir": 1, "amount": 1.0, "speed_dps": 5.0, "frame": "joint"},
                     tok=tok, timeout=120)
        print("    ok=%s  %s" % (mv.get("ok"), mv.get("error") or ""))
        for s in ((mv.get("detail") or {}).get("steps") or []):
            print("      %-8s %s" % (s.get("step"), s.get("msg")))
        time.sleep(1.0)
        j1 = joints(st(tok))
        dev = round(j1[5] - j0[5], 3)
        others = [round(b - a, 3) for a, b in zip(j0[:5], j1[:5])]
        print("    后 J6=%.3f   位移=%+.3f°   其余轴变化=%s" % (j1[5], dev, others))
        ok_m = bool(mv.get("ok")) and abs(dev) > 0.05 and all(abs(x) < 0.05 for x in others)
        results.append(("移动：J6 真实位移且仅 J6 动", ok_m,
                        "" if ok_m else "位移 %+.3f 其余 %s" % (dev, others)))

        print("\n[3b] 轴锁验证：尝试动 J1（必须被拒）")
        _, m1 = call("POST", "/api/control/jog/step",
                     {"joint": 1, "dir": 1, "amount": 1.0, "speed_dps": 5.0, "frame": "joint"},
                     tok=tok, timeout=60)
        rejected = (not m1.get("ok")) and ("轴锁" in (m1.get("error") or ""))
        print("    ok=%s  %s" % (m1.get("ok"), (m1.get("error") or "")[:70]))
        results.append(("轴锁：J1 被拒", rejected, ""))
    else:
        print("\n[3] 跳过真实位移（--no-move）")

    # ---------- 4) 吸气 / 停止吸气 ----------
    print("\n[4] 吸气")
    _, v1 = call("POST", "/api/control/vacuum", {"action": "suck"}, tok=tok, timeout=30)
    print("    ok=%s  %s" % (v1.get("ok"), v1.get("detail")))
    results.append(("吸气", bool(v1.get("ok")), str(v1.get("detail", ""))))

    time.sleep(1.0)
    print("\n[5] 停止吸气")
    _, v2 = call("POST", "/api/control/vacuum", {"action": "release"}, tok=tok, timeout=30)
    print("    ok=%s  %s" % (v2.get("ok"), v2.get("detail")))
    results.append(("停止吸气", bool(v2.get("ok")), str(v2.get("detail", ""))))

    sf = st(tok)
    print("\n[6] 收尾: prog=%s bits=%s alarm=%s/%s" % (
        sf.get("prog"), bits(sf), sf.get("alarm1"), sf.get("alarm2")))

    print("\n================ 验收结果 ================")
    for name, ok, note in results:
        print("  %s %s%s" % ("✅" if ok else "❌", name, ("   " + note) if note and not ok else ""))
    print("\n全部通过" if all(r[1] for r in results) else "\n有未通过项，见上")
    return 0 if all(r[1] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
