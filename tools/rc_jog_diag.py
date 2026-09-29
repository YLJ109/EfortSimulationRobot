# -*- coding: utf-8 -*-
"""判定 210 里到底有没有"点动段"：读关节角 → 置 Bit0 → 再读关节角。

(a) 关节角完全不变  → 210 没有点动段（触发位无人读，程序块不存在）
(b) 关节角变了但无完成位 → 210 有点动段，但完成位没回写
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, tok=None, timeout=60):
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


def joints(tok):
    st, d = call("GET", "/api/rc-status", tok=tok)
    if st != 200:
        return None, d
    j = d.get("joints")
    if j:
        return [round(float(x), 3) for x in j], d
    return None, d


def main():
    st, d = call("POST", "/api/auth/login", {"password": "admin123"})
    tok = d["token"]
    call("POST", "/api/control/run-mode", {"mode": "AUTO", "note": "diag"}, tok=tok)

    print("=== /api/pose 原始返回 ===")
    st, p = call("GET", "/api/pose", tok=tok)
    print("http=%s  %s" % (st, json.dumps(p, ensure_ascii=False)[:300]))

    j0, raw0 = joints(tok)
    print("\n关节角(前): %s" % j0)
    print("状态: prog=%s run=%s loaded=%s" % (
        raw0.get("prog"), (raw0.get("bits") or {}).get("run"),
        (raw0.get("bits") or {}).get("prog_loaded")))

    print("\n--- 尝试 J6 +1.0° @5°/s ---")
    st, r = call("POST", "/api/control/jog/step",
                 {"joint": 6, "dir": 1, "amount": 1.0, "speed_dps": 5.0, "frame": "joint"},
                 tok=tok, timeout=90)
    print("http=%s ok=%s err=%s" % (st, r.get("ok"), r.get("error")))
    print("target=%s" % r.get("target"))

    time.sleep(1.5)
    j1, raw1 = joints(tok)
    print("\n关节角(后): %s" % j1)
    if j0 and j1:
        diff = [round(b - a, 3) for a, b in zip(j0, j1)]
        print("各轴变化: %s" % diff)
        moved = any(abs(x) >= 0.05 for x in diff)
        print("\n结论: %s" % ("(b) 机器人动了 → 210 有点动段但完成位未回写"
                             if moved else
                             "(a) 机器人完全没动 → 210 里**没有点动段**（触发位无人读）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
