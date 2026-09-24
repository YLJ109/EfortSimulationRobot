# -*- coding: utf-8 -*-
"""
颜色分拣 · 相机服务集成测试（不需要真相机，全在同一进程内完成）。

为什么这么写：
  沙箱/CI 里"起一个后台服务再跨命令访问"会被回收（进程随父命令结束而消失），
  所以这里把 ThreadingHTTPServer 起在**本进程**的线程里，用 urllib 直接打；
  同时用一个喂帧线程冒充抓帧线程，把合成画面塞进 SERVICE.latest_frame。
  这样验证的是真 HTTP 路由 + 真颜色线程 + 真落盘，不是 mock。

★ 时序很重要：MOG2 会把"从第一帧就存在"的东西学成背景，所以喂帧分两个相位 ——
  相位 A 先反复喂**空背景**让 MOG2 建模，再放上物体才会被判为前景。
  （这正是生产里的真实时序：相机开起来时传送带是空的。）

覆盖：
  1. /vision/status 支持与开关
  2. 相位 A：MOG2 路径下静止物体**只触发一次**，颜色判定正确
  3. 触发后三件套落盘（full/crop/mask）且文件真实存在
  4. /vision/last?since=N 的序号语义（前端靠它去重）
  5. /vision/records、/vision/stats、按颜色过滤、/vision/records/clear
  6. /vision/image?p= 取图 + **目录穿越必须被拒**
  7. /vision/detect 同步单帧
  8. /vision/config 的 ROI / 节流参数生效
  9. 相位 B：/vision/background 静态背景路径也能独立触发；可 reset 回 MOG2
 10. /vision/calibrate/gray 白平衡、/vision/calibrate/sample 实物标定

运行（相机 venv）：
  D:\\EFORT_Projects\\EFORT_Camera_Python_OpenCv\\venv\\Scripts\\python.exe camera\\tools\\vision_service_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
CAM_DIR = os.path.dirname(HERE)
sys.path.insert(0, CAM_DIR)

import numpy as np                                     # noqa: E402
from PIL import Image                                  # noqa: E402

import camera_service as cs                            # noqa: E402

pass_n = 0
fail_n = 0


def ok(name, cond, extra=""):
    global pass_n, fail_n
    if cond:
        pass_n += 1
        print("  PASS  " + name)
    else:
        fail_n += 1
        print("  FAIL  " + name + (("   -> " + extra) if extra else ""))


BG = 118
W, H = 640, 480
COLOR_BGR = {"红": (32, 32, 224), "蓝": (216, 79, 31), "白": (255, 255, 255)}


def make_frame(name=None, jitter=0):
    """合成一帧: 中灰背景 (+ 可选的长方形物体)。"""
    img = np.full((H, W, 3), BG, np.uint8)
    if name:
        b, g, r = COLOR_BGR[name]
        x0, y0 = 180 + jitter, 160
        img[y0:y0 + 150, x0:x0 + 260] = (b, g, r)
    return Image.fromarray(img[:, :, ::-1], "RGB")     # BGR -> RGB


def http(method, url, body=None, raw=False):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            payload = r.read()
            if raw:
                return r.status, payload, r.headers.get("Content-Type")
            return r.status, json.loads(payload.decode("utf-8")), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload.decode("utf-8")), e.headers.get("Content-Type")
        except Exception:
            return e.code, {"raw": payload[:120]}, e.headers.get("Content-Type")


class Scene:
    """喂帧线程共享的"当前画面"状态。"""

    def __init__(self):
        self.name = None
        self.lock = threading.Lock()

    def set(self, name):
        with self.lock:
            self.name = name

    def get(self):
        with self.lock:
            return self.name


def wait_event(base, since, timeout=12.0, want=None):
    """轮询 /vision/last 直到出现新事件（可指定颜色）。"""
    end = time.time() + timeout
    while time.time() < end:
        _, j, _ = http("GET", base + "/vision/last?since=%d" % since)
        ev = j.get("event")
        if ev and (want is None or ev.get("color") == want):
            return ev
        time.sleep(0.15)
    return None


def main():
    S = cs.SERVICE
    base = None
    srv = None
    stop_feed = threading.Event()
    tmp_calib_dir = tempfile.mkdtemp(prefix="efort_calib_")
    old_calib_path = cs.CALIB_PATH
    old_calib = S.color_calib
    old_vision_dir = cs.VISION_DIR
    tmp_vision = tempfile.mkdtemp(prefix="efort_vision_")
    cs.VISION_DIR = tmp_vision
    cs.CALIB_PATH = os.path.join(tmp_calib_dir, "calib.json")
    scene = Scene()

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
        port = srv.server_address[1]
        base = "http://127.0.0.1:%d" % port
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2},
                         daemon=True).start()

        S.running = True

        def feeder():
            while not stop_feed.is_set():
                with S.lock:
                    S.latest_frame = make_frame(scene.get())
                time.sleep(0.05)

        threading.Thread(target=feeder, daemon=True).start()

        print("== 1. /vision/status ==")
        scene.set(None)
        time.sleep(0.3)
        st, j, _ = http("GET", base + "/vision/status")
        ok("路由可用且 supported=True", st == 200 and j.get("supported") is True, str(j)[:160])
        ok("默认未启用", j.get("enabled") is False, "enabled=%s" % j.get("enabled"))
        ok("带出色卡(18 类)", len(j.get("card") or {}) == 18, "n=%d" % len(j.get("card") or {}))
        ok("默认分割方式为 MOG2", j.get("seg") == "MOG2", "seg=%s" % j.get("seg"))

        print("== 8. /vision/config 启用 + ROI + 节流 ==")
        st, j, _ = http("POST", base + "/vision/config", {"enabled": True})
        ok("启用成功", st == 200 and j["status"]["vision"]["enabled"] is True, str(j)[:160])
        st, j, _ = http("POST", base + "/vision/config", {"roi": [40, 40, 560, 400]})
        ok("ROI 生效", j["status"]["vision"]["roi"] == [40, 40, 560, 400],
           "roi=%s" % j["status"]["vision"]["roi"])
        st, j, _ = http("POST", base + "/vision/config", {"min_interval": 0.5})
        ok("节流参数可下发", abs(j["status"]["vision"]["min_interval"] - 0.5) < 1e-6,
           "min_interval=%s" % j["status"]["vision"]["min_interval"])
        st, j, _ = http("POST", base + "/vision/config", {"use_mog2": True})
        ok("分割方式保持 MOG2", j["status"]["vision"]["seg"] == "MOG2",
           "seg=%s" % j["status"]["vision"]["seg"])

        print("== 2/3. 相位 A：MOG2 路径，静止物体只触发一次 + 三件套落盘 ==")
        time.sleep(2.2)                       # 先喂空背景，让 MOG2 建模
        scene.set("红")
        ev = wait_event(base, 0, timeout=12.0, want="红")
        ok("稳定后触发了事件", ev is not None, "12s 内未触发")
        if ev:
            ok("颜色判定为「红」", ev.get("color") == "红", "color=%s" % ev.get("color"))
            ok("事件带完整判据字段",
               all(k in ev for k in ("conf", "de", "lab", "box", "center", "ratio", "seq")),
               "keys=%s" % sorted(ev.keys()))
            ok("判定置信度不为空", ev.get("conf") is not None, "conf=%s" % ev.get("conf"))
            imgs = ev.get("images") or {}
            ok("三件套路径齐全", set(imgs) == {"full", "crop", "mask"}, str(imgs))
            missing = [k for k, v in imgs.items()
                       if not os.path.isfile(os.path.join(cs.VISION_DIR, v))]
            ok("三件套文件真实落盘", not missing, "缺失: %s" % missing)
            ok("images 是相对路径(前端可直接取图)",
               all(not os.path.isabs(v) for v in imgs.values()), str(imgs))
            ok("事件 dir 为绝对路径(运维可查)",
               bool(ev.get("dir")) and os.path.isdir(ev["dir"]), str(ev.get("dir")))

            print("== 6. /vision/image 取图 + 目录穿越拦截 ==")
            # 存档目录名含中文 → 必须做 URL 编码（前端用 encodeURIComponent 同理）
            st, body, ctype = http(
                "GET", base + "/vision/image?p=" + quote(imgs["full"], safe="/"), raw=True)
            ok("按相对路径取到带框图", st == 200 and ctype == "image/jpeg" and len(body) > 500,
               "st=%s ctype=%s len=%s" % (st, ctype, len(body)))
            st, _, _ = http("GET", base + "/vision/image?p=../../../camera_service.py")
            ok("目录穿越被拒(404)", st == 404, "st=%s" % st)
            st, _, _ = http("GET", base + "/vision/image?p=..%2F..%2Fcamera_service.py")
            ok("编码后的穿越同样被拒", st == 404, "st=%s" % st)
            st, _, _ = http("GET", base + "/vision/image?p=nope/none.jpg")
            ok("不存在的图 404", st == 404, "st=%s" % st)

        print("== 4. /vision/last 的 since 语义 ==")
        _, j, _ = http("GET", base + "/vision/last?since=0")
        seq_a = j.get("seq", 0)
        _, j, _ = http("GET", base + "/vision/last?since=%d" % (seq_a + 10))
        ok("since 大于当前序号时不返回事件", j.get("event") is None,
           "seq=%s event=%s" % (j.get("seq"), j.get("event")))
        _, j, _ = http("GET", base + "/vision/last?since=%d" % (seq_a - 1))
        ok("since 小于当前序号时返回事件", j.get("event") is not None, str(j)[:120])

        print("== 5. records / stats / 过滤 / clear ==")
        _, j, _ = http("GET", base + "/vision/records?limit=10")
        ok("记录列表可用", len(j.get("records") or []) >= 1,
           "n=%d" % len(j.get("records") or []))
        ok("统计包含该颜色", (j.get("stats") or {}).get("by_color", {}).get("红", 0) >= 1,
           str(j.get("stats")))
        _, j, _ = http("GET", base + "/vision/records?color=" + quote("蓝"))
        ok("按颜色过滤生效", all(r["color"] == "蓝" for r in j.get("records") or []),
           str(j.get("records"))[:120])

        print("== 7. /vision/detect 同步单帧 ==")
        _, j, _ = http("GET", base + "/vision/detect")
        ok("同步检测返回颜色结果",
           bool(j.get("results")) and j["results"][0]["color"]["name"] == "红",
           str(j)[:200])
        ok("同步检测不外泄 mask 数组", "mask" not in j, str(list(j.keys())))

        print("== 2b. 同一静止物体不重复触发 ==")
        _, j0, _ = http("GET", base + "/vision/last?since=0")
        seq0 = j0.get("seq", 0)
        time.sleep(2.0)
        _, j1, _ = http("GET", base + "/vision/last?since=0")
        ok("2 秒内序号未增长", j1.get("seq", 0) == seq0, "seq %s -> %s" % (seq0, j1.get("seq")))

        print("== 9. 相位 B：静态背景路径独立触发 ==")
        scene.set(None)                       # 先清空，让稳定器复位
        time.sleep(1.6)
        st, j, _ = http("POST", base + "/vision/background", {})
        ok("固化静态背景成功", st == 200 and j.get("ok") is True, str(j)[:160])
        ok("status 显示 seg=静态背景", j["status"]["vision"]["seg"] == "静态背景",
           "seg=%s" % j["status"]["vision"]["seg"])
        _, jb, _ = http("GET", base + "/vision/last?since=0")
        seq_b = jb.get("seq", 0)
        scene.set("蓝")
        ev_b = wait_event(base, seq_b, timeout=12.0, want="蓝")
        ok("静态背景下也能触发且判为「蓝」", ev_b is not None,
           "未触发; 当前颜色=%s" % (ev_b or {}).get("color"))

        st, j, _ = http("POST", base + "/vision/background", {"reset": True})
        ok("可切回 MOG2", j["status"]["vision"]["seg"] == "MOG2",
           "seg=%s" % j["status"]["vision"]["seg"])

        print("== 10. 标定: 灰卡白平衡 + 实物采样 ==")
        st, j, _ = http("POST", base + "/vision/calibrate/gray", {"target": 118.0})
        g = (j.get("info") or {}).get("gains")
        ok("灰卡增益为 3 分量", st == 200 and isinstance(g, list) and len(g) == 3, str(j)[:160])
        ok("增益落在合理区间", bool(g) and all(0.3 <= float(v) <= 3.0 for v in g), str(g))
        ok("calib.json 已写出", os.path.isfile(cs.CALIB_PATH), cs.CALIB_PATH)
        st, j, _ = http("POST", base + "/vision/calibrate/sample", {"name": "蓝"})
        ok("实物采样标定成功", st == 200 and j.get("ok") is True, str(j)[:160])
        ok("status 显示 1 个已标定色",
           (j["status"]["vision"]["calib"] or {}).get("card_n") == 1,
           str(j["status"]["vision"]["calib"]))
        with open(cs.CALIB_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        ok("calib.json 内容正确(含 gains 与 card.蓝)",
           bool(saved.get("gains")) and "蓝" in (saved.get("card") or {}), str(saved)[:200])
        st, j, _ = http("POST", base + "/vision/calibrate/sample", {"name": ""})
        ok("空颜色名被拒", st == 200 and j.get("ok") is False, str(j)[:120])
        st, j, _ = http("POST", base + "/vision/calibrate/sample", {"name": "红", "lab": [1, 2]})
        ok("非法 Lab 被拒", st == 200 and j.get("ok") is False, str(j)[:120])

        print("== 11. 清空记录 + 关闭 ==")
        st, j, _ = http("POST", base + "/vision/records/clear", {})
        ok("清空成功", st == 200 and j.get("ok") is True, str(j)[:120])
        _, j, _ = http("GET", base + "/vision/records")
        ok("清空后列表为空", (j.get("records") or []) == [], str(j)[:120])
        st, j, _ = http("POST", base + "/vision/config", {"enabled": False})
        ok("可关闭", j["status"]["vision"]["enabled"] is False, str(j)[:120])

        print("== 12. 未知路径仍 404 ==")
        st, _, _ = http("GET", base + "/vision/nope")
        ok("未知路径 404", st == 404, "st=%s" % st)

    finally:
        stop_feed.set()
        S.running = False
        S.color_enabled = False
        time.sleep(0.3)
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        cs.VISION_DIR = old_vision_dir
        cs.CALIB_PATH = old_calib_path
        S.color_calib = old_calib
        shutil.rmtree(tmp_vision, ignore_errors=True)
        shutil.rmtree(tmp_calib_dir, ignore_errors=True)

    print("")
    print("结果: %d 通过 / %d 失败" % (pass_n, fail_n))
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
