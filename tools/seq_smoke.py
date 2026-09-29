# -*- coding: utf-8 -*-
"""序列编辑器接口冒烟测试（**不下发任何运动指令**）。

覆盖：四类操作硬校验 / 名称与路径穿越防护 / 保存与载入 / 试运行(dry_run)解析 /
      进度与暂停接口的空态 / 手动下发在序列执行期间被拒（该项需真机，见 rc_full_test）。
"""
import json
import os
import sys
import urllib.error
import urllib.parse
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
PROG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "programs")
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, tok=None, timeout=30, query=None):
    # ★ 查询参数一律 urlencode：中文序列名直接塞进 URL 会让 urllib 抛
    #   UnicodeEncodeError（浏览器 fetch 会自动编码，这里必须显式做）。
    if query:
        path = path + "?" + urllib.parse.urlencode(query)
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


def main():
    ok_all = True

    def check(tag, cond, info=""):
        nonlocal ok_all
        print("  %s %s%s" % ("✅" if cond else "❌", tag, ("   " + str(info)) if info else ""))
        if not cond:
            ok_all = False

    _, d = call("POST", "/api/auth/login", {"password": _RC_PW()})
    tok = d.get("token")
    print("登录:", "OK" if tok else "失败")
    if not tok:
        return 1

    print("\n[1] 空态：进度 / 载入不存在的序列")
    st, r = call("GET", "/api/control/run-state", tok=tok)
    check("run-state 可读（空态无异常）", st == 200 and r.get("ok") is True, r.get("state"))
    # ★ 不再断言 state 必须为 None：后端**故意**保留最近一次执行的终态
    #   （_LAST_RUN_ID），好让界面在收尾后仍能显示"完成/已停止"。
    #   这里只要求"没有正在跑的"。
    _s = r.get("state")
    check("没有进行中的执行", (not r.get("active")) and (_s is None or _s.get("running") is False),
          "active=%s running=%s" % (r.get("active"), (_s or {}).get("running")))
    st, r = call("GET", "/api/control/seq", tok=tok, query={"name": "__nope__"})
    check("载入不存在的序列 → 404", st == 404, r.get("detail"))

    print("\n[2] 操作类型硬校验（后端是唯一入口，只允许六类）")
    bad = [
        ({"type": "loop"}, "循环"),
        ({"type": "if"}, "条件"),
        ({"type": "call"}, "子程序"),
        ({"type": "suck"}, None),                      # 合法
        ({"type": "blow"}, None),                      # ★ 合法（放气）
    ]
    for it, label in bad:
        st, r = call("POST", "/api/control/seq",
                     {"name": "smoke-bad", "items": [it]}, tok=tok)
        if label is None:
            check("合法步骤 %s 被接受" % it.get("type"), st == 200, r.get("detail"))
        else:
            check("拒绝非四类操作「%s」" % label, st == 400, (r.get("detail") or "")[:60])

    print("\n[3] 参数校验")
    st, r = call("POST", "/api/control/seq", {"name": "x", "items": []}, tok=tok)
    check("空序列被拒", st == 400, (r.get("detail") or "")[:50])
    st, r = call("POST", "/api/control/seq",
                 {"name": "x", "items": [{"type": "point"}]}, tok=tok)
    check("标记点缺 point_id 被拒", st == 400, (r.get("detail") or "")[:50])
    st, r = call("POST", "/api/control/seq",
                 {"name": "x", "items": [{"type": "wait", "seconds": 99999}]}, tok=tok)
    check("等待时长越界被拒", st == 400, (r.get("detail") or "")[:50])
    st, r = call("POST", "/api/control/seq",
                 {"name": "../../evil", "items": [{"op": "suck"}]}, tok=tok)
    check("路径穿越名称被拒", st == 400, (r.get("detail") or "")[:50])
    st, r = call("POST", "/api/control/seq",
                 {"name": "a/b", "items": [{"op": "suck"}]}, tok=tok)
    check("含路径分隔符名称被拒", st == 400, (r.get("detail") or "")[:50])

    print("\n[4] 保存 → 载入 回环")
    items = [
        {"type": "point", "point_id": 1},
        {"type": "suck"},
        {"type": "wait", "seconds": 0.2},
        {"type": "release"},
        {"type": "blow"},            # ★ 放气（破真空脱件）
    ]
    st, r = call("POST", "/api/control/seq",
                 {"name": "smoke-序列A", "items": items, "speed_pct": 5}, tok=tok)
    check("保存五步序列", st == 200 and r.get("ok"), r.get("file"))
    saved_file = r.get("file")
    st, r2 = call("GET", "/api/control/seq", tok=tok, query={"name": "smoke-序列A"})
    check("载入回填", st == 200 and len(r2.get("items") or []) == 5,
          json.dumps(r2.get("items"), ensure_ascii=False)[:150])

    print("\n[5] 试运行（dry_run，绝不下发）解析编辑器缓冲")
    st, r = call("POST", "/api/control/run-file",
                 {"items": items, "name": "smoke-序列A", "dry_run": True, "speed_pct": 5},
                 tok=tok)
    check("items 模式可执行（dry_run）", st == 200 and r.get("ok") is True,
          "kind=%s count=%s" % (r.get("kind"), r.get("count")))
    ops = [(s.get("index"), s.get("op") or "point", s.get("ok")) for s in (r.get("steps") or [])]
    print("      步骤解析:", ops)
    check("五步全部解析成功", len(ops) == 5 and all(x[2] is not False for x in ops))
    check("io 步骤被识别为 suck/wait/release/blow",
          [x[1] for x in ops] == ["point", "suck", "wait", "release", "blow"], ops)
    check("dry_run 不下发（readonly）", r.get("readonly") is True)

    print("\n[6] run-file 缺目标时给 422")
    st, r = call("POST", "/api/control/run-file", {"dry_run": True}, tok=tok)
    check("既无 filename 又无 items → 422", st == 422, st)

    print("\n[7] 暂停/继续 空态")
    st, r = call("POST", "/api/control/run-pause", {}, tok=tok)
    check("无执行时暂停 → 明确提示", st == 200 and r.get("ok") is False, r.get("error"))
    st, r = call("POST", "/api/control/run-resume", {}, tok=tok)
    check("无暂停时继续 → 明确提示", st == 200 and r.get("ok") is False, r.get("error"))

    print("\n[7b] 吸放动作入参闸门（读 OpenAPI 校验，**零副作用**）")
    # ★ 为什么不直接 POST：/control/vacuum 是**真写寄存器**的接口 ——
    #   控制器在 AUTO 时 POST 一次就真的吸一次/放一次气（本脚本不该动机器）。
    #   这里改为读 openapi.json 里 VacuumIn.action 的正则，纯校验、不出手。
    st, spec = call("GET", "/openapi.json", tok=tok)
    pat = (((spec.get("components") or {}).get("schemas") or {})
           .get("VacuumIn", {}).get("properties", {}).get("action", {}).get("pattern"))
    check("能取到 VacuumIn.action 的正则", bool(pat), pat)
    import re as _re
    rx = _re.compile(pat) if pat else None
    for act in ("suck", "release", "blow"):
        check("动作 %s 在允许集内" % act, bool(rx and rx.match(act)), pat)
    check("未知动作 purge 不在允许集内", bool(rx and not rx.match("purge")), pat)

    print("\n[8] 清理冒烟文件")
    for fn in (saved_file, "smoke-bad.json"):
        if not fn:
            continue
        pth = os.path.abspath(os.path.join(PROG_DIR, fn))
        try:
            if os.path.isfile(pth):
                os.remove(pth)
                print("      已删除", fn)
        except OSError as e:
            print("      删除失败", fn, e)

    print("\n================ 冒烟结果 ================")
    print("全部通过" if ok_all else "有失败项，见上 ❌")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
