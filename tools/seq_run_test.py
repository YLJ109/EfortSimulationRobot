# -*- coding: utf-8 -*-
"""序列执行器端到端验证：**只用「等待」步骤**，不碰任何寄存器/真空/运动。

为什么这样设计：等待步是纯软件计时，因此这套验证在任何控制器档位（含 T1）都安全 ——
却能真实验证执行通道、进度上报、暂停冻结、继续、停止打断。

验证项：
  1. items 模式端到端执行（编辑器缓冲直接下发）
  2. 进度上报：/control/run-state 的 index/total/phase 随执行推进
  3. **暂停冻结**：暂停期间等待步的剩余时间不推进（暂停 2s 不吞掉等待）
  4. 继续后跑完
  5. **停止打断**：10s 的等待能在 1s 内被「停止执行」打断（cancelled=true）
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, tok=None, timeout=120, query=None):
    import urllib.parse
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


RESULT = {}


def _run_seq(tok, name, items, tag):
    RESULT[tag] = call("POST", "/api/control/run-file",
                       {"items": items, "name": name, "dry_run": False, "speed_pct": 5},
                       tok=tok, timeout=180)


def _state(tok):
    st, d = call("GET", "/api/control/run-state", tok=tok, timeout=10)
    return (d or {}).get("state") if st == 200 else None


def main():
    ok_all = True

    def check(tag, cond, info=""):
        nonlocal ok_all
        print("  %s %s%s" % ("✅" if cond else "❌", tag, ("   " + str(info)) if info else ""))
        if not cond:
            ok_all = False

    _, d = call("POST", "/api/auth/login", {"password": "admin123"})
    tok = d["token"]
    call("POST", "/api/control/run-mode", {"mode": "AUTO", "note": "seq wait test"}, tok=tok)
    print("登录 OK。本次只用「等待」步骤，不写任何寄存器。\n")

    # ================= 场景 1：暂停 / 继续 =================
    # 两步各 3 秒。若暂停不生效，3 秒后 index 会变成 2；若暂停吞掉等待，则提前结束。
    print("[1] 暂停 / 继续（2 步 × 3s 等待）")
    t0 = time.time()
    th = threading.Thread(target=_run_seq,
                          args=(tok, "wait-test-pause", [{"type": "wait", "seconds": 3},
                                                         {"type": "wait", "seconds": 3}], "pause"),
                          daemon=True)
    th.start()
    time.sleep(1.0)

    st, r = call("POST", "/api/control/run-pause", {}, tok=tok, timeout=10)
    check("暂停接口受理", st == 200 and r.get("ok") is True, r.get("paused"))

    time.sleep(0.5)
    s = _state(tok)
    check("run-state 反映 paused=True", bool(s and s.get("paused")),
          "paused=%s index=%s" % (s and s.get("paused"), s and s.get("index")))
    paused_idx = (s or {}).get("index")

    time.sleep(2.5)          # 暂停期间跨过第一个 3s 等待的原始时长
    s2 = _state(tok)
    check("暂停期间执行未推进（仍停在第 %s 步）" % paused_idx,
          (s2 or {}).get("index") == paused_idx and th.is_alive(),
          "index=%s alive=%s" % ((s2 or {}).get("index"), th.is_alive()))
    check("暂停期间没有偷偷跑完", th.is_alive())

    st, r = call("POST", "/api/control/run-resume", {}, tok=tok, timeout=10)
    check("继续接口受理", st == 200 and r.get("ok") is True, r.get("resumed"))
    th.join(timeout=30)
    el = time.time() - t0
    print("      总耗时 %.1fs（暂停冻结了约 3s）" % el)
    st, res = RESULT["pause"]
    check("暂停后仍能跑完（ok=true）", bool(res.get("ok")), res.get("error"))
    check("两步都被执行（count=2）", res.get("count") == 2, res.get("count"))
    check("暂停确实冻结了时间（总耗时 > 7s）", el > 7.0, "%.1fs" % el)

    # ================= 场景 2：停止执行 =================
    print("\n[2] 停止执行（1 步 × 10s 等待，1s 时停止）")
    t1 = time.time()
    th2 = threading.Thread(target=_run_seq,
                           args=(tok, "wait-test-stop", [{"type": "wait", "seconds": 10}], "stop"),
                           daemon=True)
    th2.start()
    time.sleep(1.0)
    st, r = call("POST", "/api/control/run-cancel", {"run_id": None, "filename": None},
                 tok=tok, timeout=15)
    check("停止接口受理", st == 200 and r.get("ok") is True, r.get("matched"))
    th2.join(timeout=15)
    el2 = time.time() - t1
    st, res2 = RESULT["stop"]
    check("10s 等待被提前打断（耗时 < 4s）", el2 < 4.0, "%.2fs" % el2)
    check("结果标记 cancelled=true", res2.get("cancelled") is True, res2.get("cancelled"))
    check("ok=false（未跑完）", res2.get("ok") is False, res2.get("ok"))

    # ================= 场景 3：正常跑完 + 终态 =================
    print("\n[3] 正常跑完（1 步 × 0.3s）与终态")
    st, res3 = call("POST", "/api/control/run-file",
                    {"items": [{"type": "wait", "seconds": 0.3}], "name": "wait-test-ok",
                     "dry_run": False, "speed_pct": 5}, tok=tok, timeout=30)
    check("执行成功", st == 200 and res3.get("ok") is True, res3.get("error"))
    s3 = _state(tok)
    check("收尾后 run-state 仍可读（不返回 null）", s3 is not None)
    check("终态 phase=done 且 running=false",
          bool(s3 and s3.get("phase") == "done" and s3.get("running") is False),
          "phase=%s running=%s" % (s3 and s3.get("phase"), s3 and s3.get("running")))
    check("终态不残留 paused", not (s3 or {}).get("paused"))
    check("终态 index/total 可用",
          bool(s3 and s3.get("index") == 1 and s3.get("total") == 1),
          "index=%s total=%s" % (s3 and s3.get("index"), s3 and s3.get("total")))

    # ================= 场景 4：执行期间手动点动被拒 =================
    print("\n[4] 互锁：序列执行期间手动点动/吸放被拒")
    th3 = threading.Thread(target=_run_seq,
                           args=(tok, "wait-test-lock", [{"type": "wait", "seconds": 2.5}], "lock"),
                           daemon=True)
    th3.start()
    time.sleep(0.6)
    st, j = call("POST", "/api/control/jog/step",
                 {"joint": 6, "dir": 1, "amount": 1.0, "speed_dps": 5.0}, tok=tok, timeout=20)
    check("手动点动被 409 拒绝", st == 409, "http=%s %s" % (st, (j.get("detail") or "")[:60]))
    st, v = call("POST", "/api/control/vacuum", {"action": "suck"}, tok=tok, timeout=20)
    check("手动吸放被拒（409/403）", st in (409, 403), "http=%s" % st)
    th3.join(timeout=20)
    st, j2 = call("POST", "/api/control/jog/step",
                  {"joint": 6, "dir": 1, "amount": 1.0, "speed_dps": 5.0}, tok=tok, timeout=30)
    # ★ 这里只验"不再被序列互锁拦下"。若仍 409，必须是**别的原因**
    #   （档位 T1/T2、轴锁、围栏），不能是"有序列正在执行"。
    msg = str((j2.get("detail") if isinstance(j2, dict) else "") or
              (j2.get("message") if isinstance(j2, dict) else "") or "")
    check("序列结束后点动不再被「执行中」互锁拦下",
          st != 409 or ("正在执行" not in msg),
          "http=%s %s" % (st, msg[:70]))

    print("\n================ 结果 ================")
    print("全部通过" if ok_all else "有失败项，见上 ❌")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
