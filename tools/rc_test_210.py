# -*- coding: utf-8 -*-
"""EFORT 真机实测：加载运行 210（三合一）→ 移动 → 吸气 → 停止吸气。

安全口径（现场既定）：
  · 控制器必须 AUTO（本脚本先断言）
  · 只动 J6，单次 1°，慢速；**J1~J5 只在轴锁护栏开启时才做"应被拒"试探**，
    护栏关闭（生产默认）时一律跳过，绝不主动去动 J1~J5
  · 真实下发总闸 EFORT_REAL_MOTION=1 + motion.real_write=true（后端 .env 已开）

用法： python tools/rc_test_210.py            # 全流程
       python tools/rc_test_210.py --no-move  # 不动机器人，只测吸气/停止吸气
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
PROG = 210
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, tok=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if tok:
        h["X-Control-Token"] = tok
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
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


def pose():
    st, d = call("GET", "/api/pose")
    if st != 200:
        return None
    j = d.get("joints") or d.get("pose") or d
    return j if isinstance(j, list) else None


def show(tag, st, d, limit=520):
    s = json.dumps(d, ensure_ascii=False)
    if len(s) > limit:
        s = s[:limit] + " …"
    print("  [%s] http=%s  %s" % (tag, st, s))


def main():
    do_move = "--no-move" not in sys.argv

    st, d = call("POST", "/api/auth/login", {"password": "admin123"})
    if st != 200:
        print("!! 登录失败", st, d)
        return 1
    tok = d["token"]
    print("1) 登录 OK (ttl=%ss)" % d.get("ttl"))

    st, rm = call("GET", "/api/control/run-mode", tok=tok)
    mode = rm.get("observed_mode") or rm.get("mode")
    print("2) 控制器档位 = %s (joggable=%s, source=%s)" % (mode, rm.get("joggable"), rm.get("source")))
    if mode != "AUTO":
        print("!! 控制器不在 AUTO，按铁律不下发任何指令。请在示教器切到 AUTO 后重跑。")
        return 2
    st, c = call("POST", "/api/control/run-mode", {"mode": "AUTO", "note": "AI 实测 210"}, tok=tok)
    show("claim AUTO", st, c, 200)

    print("\n3) 一键就绪：加载并运行程序 %s" % PROG)
    st, r = call("POST", "/api/ready", {"prog": PROG}, tok=tok, timeout=90)
    show("ready", st, r, 900)
    ok_ready = bool(isinstance(r, dict) and r.get("ok"))
    if not ok_ready:
        print("!! 210 加载/运行失败 —— 见上面 error/alarm。后续动作已跳过。")
        return 3
    rcs = {}
    st, rcs = call("GET", "/api/rc-status", tok=tok)
    print("   控制器快照: prog=%s run=%s servo=%s alarm1=%s" % (
        rcs.get("prog"), (rcs.get("bits") or {}).get("run"),
        (rcs.get("bits") or {}).get("servo"), rcs.get("alarm1")))

    p0 = pose()
    print("\n4) 移动前位姿: %s" % (p0 and [round(x, 3) for x in p0]))

    if do_move:
        print("\n5) 移动测试：J6 +1.0° @ 5°/s（仅 J6，慢速）")
        st, m = call("POST", "/api/control/jog/step",
                     {"joint": 6, "dir": 1, "amount": 1.0, "speed_dps": 5.0, "frame": "joint"},
                     tok=tok, timeout=90)
        show("J6 step", st, m, 900)
        time.sleep(0.6)
        p1 = pose()
        print("   移动后位姿: %s" % (p1 and [round(x, 3) for x in p1]))
        if p0 and p1:
            print("   J6 位移 = %+.3f°" % (p1[5] - p0[5]))

        # ★★ 只在轴锁护栏**确实开启**时才去试 J1。护栏是给 AI 做真机验证用的
        #    临时开关，生产默认关闭（操作员全轴可动）；锁没开时发 J1 点动
        #    不是"验证被拒"，而是**真的把 J1 动了** —— 绝不允许。
        _, hd = call("GET", "/api/system/health")
        lock_on = bool(((hd or {}).get("joint_lock") or {}).get("enabled"))
        if lock_on:
            print("\n6) 轴锁验证（护栏已开启）：尝试动 J1，必须被拒")
            st, m1 = call("POST", "/api/control/jog/step",
                          {"joint": 1, "dir": 1, "amount": 1.0, "speed_dps": 5.0,
                           "frame": "joint"},
                          tok=tok, timeout=60)
            show("J1 step(应拒)", st, m1, 600)
        else:
            print("\n6) 轴锁未开启（生产默认：操作员全轴可动）→ 跳过 J1 试探")
    else:
        print("\n5) 跳过移动测试（--no-move）")

    print("\n7) 吸气（40135.Bit1 → 常驻程序置 DOut[N]=true，保持）")
    st, s1 = call("POST", "/api/control/vacuum", {"action": "suck"}, tok=tok, timeout=30)
    show("vacuum suck", st, s1, 900)

    print("\n8) 停止吸气（40135.Bit2 → 常驻程序置 DOut[N]=false）")
    st, s2 = call("POST", "/api/control/vacuum", {"action": "release"}, tok=tok, timeout=30)
    show("vacuum release", st, s2, 900)

    print("\n9) 收尾快照")
    st, rcs2 = call("GET", "/api/rc-status", tok=tok)
    print("   prog=%s run=%s alarm1=%s alarm2=%s" % (
        rcs2.get("prog"), (rcs2.get("bits") or {}).get("run"),
        rcs2.get("alarm1"), rcs2.get("alarm2")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
