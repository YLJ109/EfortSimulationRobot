# -*- coding: utf-8 -*-
"""EFORT 真机诊断（只读，不下发任何指令）。"""
import json
import os
import sys
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

# ★ 本机 shell 里可能存在 HTTP(S)_PROXY，会把 127.0.0.1 也代理出去（返回 502
#   upstream connect failed）。本地回环必须显式绕开代理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, tok=None, timeout=25):
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


def main():
    st, d = call("POST", "/api/auth/login", {"password": _RC_PW()})
    print("[login] http=%s" % st)
    if st != 200:
        print("  ->", d)
        return 1
    tok = d.get("token")
    print("[login] token ok, ttl=%s" % d.get("ttl"))

    for path in ("/api/system/health", "/api/control/run-mode", "/api/robot/rc-status", "/api/robot/pose"):
        st, d = call("GET", path, tok=tok)
        print("\n=== GET %s  http=%s ===" % (path, st))
        print(json.dumps(d, ensure_ascii=False, indent=2)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
