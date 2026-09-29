# -*- coding: utf-8 -*-
"""零位移点动探针：判定 210 里的 40135.Bit0 分支到底存不存在。

原理：把目标角写成**当前实测位姿**（零位移），再走一次点动链路。
  · 若程序里有 Bit0 分支 → MJOINT 走到同一个点，**照样置 40035.Bit0 完成位** → ok=True
  · 若没有 Bit0 分支 → 触发位置 1 无人读 → 完成位永不置位 → 超时
零位移 ⇒ 就算分支存在，机器人也不动。安全。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _RC_PW() -> str:
    """管理员口令：优先环境变量 EFORT_ADMIN_PASSWORD，其次项目根 .env。

    ★★ 绝不写死在源码里 —— 本仓库在 GitHub 上，明文口令等于公开。
       （2026-09-29 修正：此前这些工具里硬编码了真实口令。）
    """
    v = os.environ.get("EFORT_ADMIN_PASSWORD", "").strip()
    if v:
        return v
    try:
        env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
        with open(env, encoding="utf-8-sig") as f:
            for ln in f:
                s = ln.strip()
                if s.startswith("EFORT_ADMIN_PASSWORD="):
                    return s.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise SystemExit("未找到管理员口令：请设环境变量 EFORT_ADMIN_PASSWORD，"
                     "或在项目根 .env 里配置 EFORT_ADMIN_PASSWORD=...")



BASE = "http://127.0.0.1:8000"
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


def snap(tok):
    st, d = call("GET", "/api/rc-status", tok=tok)
    return d if st == 200 else {}


def main():
    st, d = call("POST", "/api/auth/login", {"password": _RC_PW()})
    tok = d["token"]
    call("POST", "/api/control/run-mode", {"mode": "AUTO", "note": "probe bit0"}, tok=tok)

    # --prog N：先强制一键就绪加载运行程序 N（用于区分"程序内容问题"与"环境问题"）
    force_prog = None
    for i, a in enumerate(sys.argv):
        if a == "--prog" and i + 1 < len(sys.argv):
            force_prog = int(sys.argv[i + 1])
    if force_prog is not None:
        print("--- 先就绪到程序 %d ---" % force_prog)
        st, rr = call("POST", "/api/ready", {"prog": force_prog}, tok=tok, timeout=90)
        print("ready http=%s ok=%s err=%s" % (st, rr.get("ok"), rr.get("error")))
        for s in (rr.get("steps") or []):
            print("   %-9s ok=%-5s %s" % (s.get("step"), s.get("ok"), s.get("msg")))
        time.sleep(0.4)

    s0 = snap(tok)
    cur = [round(float(x), 3) for x in s0.get("joints", [])]
    bits = {k: v for k, v in (s0.get("bits") or {}).items() if v}
    print("当前位姿 : %s" % cur)
    print("状态字   : %s (raw=%s)  prog=%s" % (bits, s0.get("status_word"), s0.get("prog")))

    # ★ 前置可判性检查：T1/T2 手动档、或程序没在跑 → PC 指令会被拒，
    #   这次探针**没有结论**（绝不能当成"缺分支"，那是误判）。
    manual = bool((s0.get("bits") or {}).get("manual"))
    auto_or_remote = bool((s0.get("bits") or {}).get("auto")
                          or (s0.get("bits") or {}).get("remote"))
    running = bool((s0.get("bits") or {}).get("run"))
    if manual and not auto_or_remote:
        print("\n!! 控制器在 T1/T2 手动档 → PC 指令一律被拒，本次探针**无结论**。")
        print("   请在示教器把模式开关拨到 AUTO，并在示教器上运行 210，然后重跑本脚本。")
        return 4
    if not running:
        # 已加载未运行 → 直接由「一键就绪」拉起（等触发，不产生运动），再重读状态
        prog = s0.get("prog") or 210
        print("\n  程序 %s 未在运行 → 调用一键就绪拉起…" % prog)
        st, rr = call("POST", "/api/ready", {"prog": int(prog)}, tok=tok, timeout=90)
        print("  ready http=%s ok=%s err=%s" % (st, rr.get("ok"), rr.get("error")))
        for s in (rr.get("steps") or []):
            print("     %-9s ok=%-5s %s" % (s.get("step"), s.get("ok"), s.get("msg")))
        time.sleep(0.4)
        s0 = snap(tok)
        cur = [round(float(x), 3) for x in s0.get("joints", [])]
        bits = {k: v for k, v in (s0.get("bits") or {}).items() if v}
        print("  拉起后状态字: %s (raw=%s)  prog=%s" % (
            bits, s0.get("status_word"), s0.get("prog")))
        print("  拉起后位姿  : %s" % cur)
        if not (s0.get("bits") or {}).get("run"):
            print("\n!! 程序仍**未运行**(run=0) → 本次探针**无结论**。请在示教器上手动运行 210。")
            return 5

    print("\n--- 零位移点动：目标 = 当前位姿，speed 5% ---")
    st, r = call("POST", "/api/control/move",
                 {"joints": cur, "speed_pct": 5}, tok=tok, timeout=90)
    print("http=%s  ok=%s" % (st, r.get("ok")))
    if st != 200:
        print("原始返回: %s" % json.dumps(r, ensure_ascii=False)[:400])
        print("\n!! 下发被前置守卫拒绝（HTTP %s）→ 本次探针**无结论**，先解决上面的拒绝原因。"
              % st)
        return 6
    if r.get("error"):
        print("error : %s" % r["error"])
    for s in ((r.get("detail") or {}).get("steps") or []):
        print("   step %-8s ok=%-5s %s" % (s.get("step"), s.get("ok"), s.get("msg")))

    time.sleep(0.5)
    s1 = snap(tok)
    cur1 = [round(float(x), 3) for x in s1.get("joints", [])]
    print("\n下发后位姿: %s  变化=%s" % (
        cur1, [round(b - a, 3) for a, b in zip(cur, cur1)] if cur and cur1 else "?"))

    print("\n================ 判定 ================")
    if r.get("ok"):
        print("★ 210 里**存在** 40135.Bit0 分支（零位移也能置完成位）")
        print("  → 之前移动失败的真因不是缺分支，需要复测真实位移。")
    else:
        print("★ 210 里**不存在**（或未生效的）40135.Bit0 分支：")
        print("  触发位已置 1 但完成位 40035.Bit0 始终不置位，机器人也零位移。")
        print("  → 需要把 programs/210_merged.XPL 导入控制器 210 后复测。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
