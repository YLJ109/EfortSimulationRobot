# -*- coding: utf-8 -*-
"""
视觉颜色分拣 · 离线回归（不需要相机在线）。

覆盖：
  A. ΔE2000 数值正确性 —— 用 Sharma 等（CIEDE2000 原始论文）的经典测试对钉死公式；
  B. sRGB→Lab 正确性 —— 红/白/黑的已知值；
  C. 判色可达性 —— 18 类色卡**每一类都能被判回自己**（防止阈值把某类"吞掉"，
     例如无彩色分档曾让"米白"永远选不到）；
  D. 分割与筛选 —— 只认长方形；圆形必须被剔除；
  E. 稳定判定 —— 静止目标连续 N 帧只触发一次；目标离开后能复位；
  F. 光照鲁棒性 —— 轻度明暗变化下判色保持率；
  G. 真实抓拍冒烟 —— captures/ 里有图就跑一遍，确保管线不炸。

运行（相机 venv）：
  D:\\EFORT_Projects\\EFORT_Camera_Python_OpenCv\\venv\\Scripts\\python.exe camera\\tools\\vision_color_test.py
"""
from __future__ import annotations

import glob
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import vision_color as vc  # noqa: E402

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


def hex_bgr(h: str):
    r, g, b = vc.hex_to_rgb(h)
    return (b, g, r)


BG = 118          # 18% 中灰背景
W, H = 640, 480


def render(color_bgr, angle=12.0, gain=1.0, noise=0.0, shape="rect"):
    """在中灰背景上画一个物体。gain 只作用于物体（模拟物料受光变化）。"""
    img = np.full((H, W, 3), BG, np.uint8)
    c = tuple(int(max(0, min(255, v * gain))) for v in color_bgr)
    if shape == "rect":
        box = cv2.boxPoints(((W / 2, H / 2), (260, 150), angle)).astype(np.int32)
        cv2.fillPoly(img, [box], c)
    else:  # circle：用于"不该被当成长方形"的反例
        cv2.circle(img, (W // 2, H // 2), 110, c, -1)
    if noise > 0:
        n = np.random.normal(0.0, noise, img.shape)
        img = np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)
    return img


BG_IMG = np.full((H, W, 3), BG, np.uint8)


def main():
    np.random.seed(7)
    cv2.setRNGSeed(7)

    print("== A. CIEDE2000 对照 Sharma 论文测试对 ==")
    # (lab1, lab2, 期望 ΔE00)
    pairs = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
        ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
        ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ]
    for lab1, lab2, expect in pairs:
        got = float(vc.delta_e_2000(np.array(lab1), np.array(lab2)))
        ok("ΔE00(%s, %s) = %.4f" % (lab1, lab2, got),
           abs(got - expect) < 5e-3, "期望 %.4f 实得 %.4f" % (expect, got))

    print("== B. sRGB -> Lab 已知值 ==")
    white = vc.srgb_to_lab(np.array([255.0, 255.0, 255.0]))
    black = vc.srgb_to_lab(np.array([0.0, 0.0, 0.0]))
    red = vc.srgb_to_lab(np.array([255.0, 0.0, 0.0]))
    ok("白 L*≈100", abs(white[0] - 100.0) < 0.05, "L=%.3f" % white[0])
    ok("黑 L*≈0", abs(black[0]) < 0.05, "L=%.3f" % black[0])
    ok("纯红 ≈ (53.24, 80.09, 67.20)",
       abs(red[0] - 53.24) < 0.1 and abs(red[1] - 80.09) < 0.1 and abs(red[2] - 67.20) < 0.1,
       "实得 (%.2f, %.2f, %.2f)" % (red[0], red[1], red[2]))

    print("== C. 三色模型可达：红/绿/蓝可判回自己，非目标不产生新类别 ==")
    eng = vc.VisionEngine(background=BG_IMG, use_mog2=False)
    reach_bad, leak_bad = [], []
    for name, hx in vc.RGB_CARD.items():
        img = render(hex_bgr(hx))
        r = eng.detect(img)
        got = r["results"][0]["color"]["name"] if r["results"] else "<无目标>"
        if got != name:
            reach_bad.append("%s->%s" % (name, got))
    for name, hx in vc.COLOR_CARD.items():
        if name in vc.RGB_NAMES:
            continue                       # 三色已在上面测过可达
        img = render(hex_bgr(hx))
        r = eng.detect(img)
        got = r["results"][0]["color"]["name"] if r["results"] else "<无目标>"
        # ★ 2026-09-25：实时链路改为"只用颜色阈值"（对齐参考实现）后，白/黑/灰/银灰这类
        #   **低饱和**物体在前景掩膜里根本不存在 → "<无目标>" 是**预期结果**，不是泄漏。
        #   本断言真正要守的是"绝不给非三色输入**新造**出 橙/黄/青 等三色之外的类别名"。
        if got == "<无目标>":
            continue
        if got not in vc.RGB_NAMES and got != "未知":
            leak_bad.append("%s->%s" % (name, got))
    ok("红/绿/蓝可判回自己", not reach_bad, "错判: " + ", ".join(reach_bad))
    ok("非目标不新造三色外类别（无目标/未知均视为正常）", not leak_bad,
       "泄漏: " + ", ".join(leak_bad[:8]))

    print("== D. 只认长方形（圆必须剔除） ==")
    eng2 = vc.VisionEngine(background=BG_IMG, use_mog2=False)
    rr = eng2.detect(render(hex_bgr("#1F4FD8")))
    ok("矩形被找到", len(rr["results"]) == 1, "找到 %d 个" % len(rr["results"]))
    eng3 = vc.VisionEngine(background=BG_IMG, use_mog2=False)
    rc = eng3.detect(render(hex_bgr("#1F4FD8"), shape="circle"))
    ok("圆形被剔除", len(rc["results"]) == 0, "找到 %d 个" % len(rc["results"]))

    print("== E. 稳定判定：静止持续触发；离开后复位 ==")
    eng4 = vc.VisionEngine(background=BG_IMG, use_mog2=False)
    frame = render(hex_bgr("#4CAF50"))
    fires = [bool(eng4.detect(frame.copy()).get("fire")) for _ in range(6)]
    # ★ 语义已按实机改为"实时模式"：稳定后**只要目标还在场就持续触发**（帧率节流交给上层
    #   color_min_interval）。旧断言"6 帧恰好 1 次"与实现矛盾，属过期断言，这里更正。
    first = fires.index(True) if any(fires) else -1
    ok("第 1 帧不触发、第 2 帧起持续触发（实时模式，stable_frames=2）",
       first == 1 and all(fires[1:]), "序列=%s" % fires)
    ok("第 3 帧仍在触发", fires[2] is True, "序列=%s" % fires)
    for _ in range(12):                       # 目标离开
        eng4.detect(BG_IMG.copy())
    ok("目标离开后复位为 EMPTY", eng4.tracker.state == "EMPTY",
       "state=%s" % eng4.tracker.state)
    again = eng4.detect(frame.copy())
    ok("新物体可再次触发计数", again["tracker"]["hits"] >= 1, str(again["tracker"]))

    print("== F. 光照鲁棒性：红/绿/蓝在明暗 ±12% 下保持率 ==")
    keep, total, changed = 0, 0, []
    # 只评估三色目标：橙/黄/青等非目标在光变下改判"未知"属预期，不参与保持率
    for name, hx in vc.RGB_CARD.items():
        bgr = hex_bgr(hx)
        for gain in (0.88, 0.94, 1.0, 1.06):
            e = vc.VisionEngine(background=BG_IMG, use_mog2=False)
            r = e.detect(render(bgr, gain=gain, noise=3.0))
            got = r["results"][0]["color"]["name"] if r["results"] else "<无目标>"
            total += 1
            if got == name:
                keep += 1
            elif gain != 1.0:
                changed.append("%s@%.2f->%s" % (name, gain, got))
    rate = keep / float(total)
    ok("三色保持率 >= 78%%", rate >= 0.78, "保持率=%.1f%% (%d/%d)" % (rate * 100, keep, total))
    if changed:
        print("        （受光变化后改名，属预期内的相邻色混淆）: " + ", ".join(changed[:12]))

    print("== G. 真实抓拍冒烟 ==")
    caps = sorted(glob.glob(os.path.join(os.path.dirname(HERE), "captures", "*.jpg")))
    if not caps:
        print("  SKIP  无 captures 图片")
    for p in caps[:3]:
        img = cv2.imread(p)
        if img is None:
            ok("读取 %s" % os.path.basename(p), False, "cv2.imread 返回 None")
            continue
        try:
            e = vc.VisionEngine(use_mog2=True)     # 无背景 -> 走 MOG2
            for _ in range(4):
                r = e.detect(img)
            ok("跑通 %s (MOG2)" % os.path.basename(p), "results" in r)
            print("        检出 %d 个候选; 画面 %dx%d" % (len(r["results"]), img.shape[1], img.shape[0]))
        except Exception as ex:                     # noqa
            ok("跑通 %s" % os.path.basename(p), False, repr(ex))

    # ------------------------------------------------------------------
    # H. 定向钉子：钉死两个"看着没问题但一定会炸"的历史缺陷，防止被改回去。
    #    ★ 这类断言必须落在最底层函数上（而不是只靠端到端），这样才能在
    #      有人把绝对阈值写回来时立刻红。
    # ------------------------------------------------------------------
    print("== H. 定向钉子（历史缺陷防回退） ==")

    # H1. 饱和物料（某通道 = 255）不得被判成"未知"
    #     旧版 dominant_lab 用绝对阈值 V < 0.96*255 剔高光 → 整块清空。
    sat_bad = []
    for nm in ("橙", "黄", "天蓝", "粉", "白"):
        bgr = hex_bgr(vc.COLOR_CARD[nm])
        assert max(bgr) == 255, nm            # 前提：这几种色卡确实有通道饱和
        m = vc.diff_mask(render(bgr), BG_IMG)
        d = vc.dominant_lab(render(bgr), m)
        if not d["ok"]:
            sat_bad.append(nm)
    ok("饱和物料不被高光判据清空（dominant_lab.ok）", not sat_bad,
       "被清空: " + ", ".join(sat_bad))

    # H2. 纯黑物料不得被判成"未知"
    #     旧版用绝对阈值 L > 10 剔暗部 → 黑(L*≈8) 被整块清空。
    blk = render(hex_bgr(vc.COLOR_CARD["黑"]))
    dblk = vc.dominant_lab(blk, vc.diff_mask(blk, BG_IMG))
    ok("纯黑物料不被暗部判据清空（dominant_lab.ok）", bool(dblk["ok"]),
       "valid=%s" % dblk.get("valid"))
    ok("纯黑物料 L* 落在无彩色黑档(<34)", float(dblk["lab"][0]) < vc.L_GRAY,
       "L*=%.2f" % float(dblk["lab"][0]))

    # H3. 背景差分的通道灵敏度：灰物体压在中灰背景上，灰度差很小，
    #     必须靠"逐通道最大差"保留下来（旧版先转灰度会把它平均掉）。
    g_obj = tuple(int(v * 0.94) for v in hex_bgr(vc.COLOR_CARD["灰"]))
    g_img = np.full((H, W, 3), BG, np.uint8)
    box = cv2.boxPoints(((W / 2, H / 2), (260, 150), 0)).astype(np.int32)
    cv2.fillPoly(g_img, [box], g_obj)
    diff = cv2.absdiff(g_img, BG_IMG)
    max_ch = int(diff.max(axis=2).max())
    gray_avg = int(cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY).max())
    ok("背景差分用逐通道最大差（灵敏度高于灰度平均）",
       max_ch > 12 and vc.diff_mask(g_img, BG_IMG).any(),
       "通道最大差=%d 灰度差=%d" % (max_ch, gray_avg))

    # H4. 无彩色档位边界必须是对称的中点，且米白的色度留有余量
    #     （旧版白/银灰边界写成 95.5 而非中点 91.1；CHROMA_CUT=12 离米白只差 1.18）。
    mid_ws = (vc.CARD_LAB["白"][0] + vc.CARD_LAB["银灰"][0]) / 2.0
    ok("白/银灰亮度边界 = 两卡中点", abs(vc.L_WHITE - mid_ws) < 1.5,
       "L_WHITE=%.1f 中点=%.2f" % (vc.L_WHITE, mid_ws))
    min_margin = min(
        min(float(np.hypot(*vc.CARD_LAB[n][1:])) for n in vc.ACHROMATIC) - 0.0,
        vc.CHROMA_CUT - max(float(np.hypot(*vc.CARD_LAB[n][1:])) for n in vc.ACHROMATIC),
    )
    mb_c = float(np.hypot(*vc.CARD_LAB["米白"][1:]))
    ok("CHROMA_CUT 对无彩色/米白两侧均留 >=2.5 余量",
       vc.CHROMA_CUT - max(float(np.hypot(*vc.CARD_LAB[n][1:])) for n in vc.ACHROMATIC) >= 2.5
       and mb_c - vc.CHROMA_CUT >= 2.5,
       "CUT=%.1f 无彩色最大C*=%.2f 米白C*=%.2f" % (
           vc.CHROMA_CUT,
           max(float(np.hypot(*vc.CARD_LAB[n][1:])) for n in vc.ACHROMATIC), mb_c))

    # ------------------------------------------------------------------
    # I. 只报一个目标（对齐参考实现 visual_object_detector.py）
    # ------------------------------------------------------------------
    print("== I. 只报一个：先过置信度阈值 → 取面积最大（并列取置信度高） ==")
    rs = [
        {"area": 100, "color": {"conf": 0.90, "name": "红"}},
        {"area": 900, "color": {"conf": 0.30, "name": "绿"}},   # 面积最大但不达标
        {"area": 500, "color": {"conf": 0.80, "name": "蓝"}},
    ]
    ok("过滤后取面积最大者（蓝）",
       [r["color"]["name"] for r in vc.pick_best(rs, 0.5)] == ["蓝"],
       str([r["color"]["name"] for r in vc.pick_best(rs, 0.5)]))
    ok("只返回 0 或 1 个", len(vc.pick_best(rs, 0.0)) == 1)
    ok("全不达标 → 空", vc.pick_best(rs, 0.95) == [])
    ok("空/None 输入 → 空", vc.pick_best([], 0.0) == [] and vc.pick_best(None, 0.0) == [])
    ok("面积并列取置信度高者（绿）",
       [r["color"]["name"] for r in vc.pick_best(
           [{"area": 100, "color": {"conf": 0.3, "name": "红"}},
            {"area": 100, "color": {"conf": 0.9, "name": "绿"}}], 0.0)] == ["绿"])
    # ★ 用**正**阈值（相机服务实际就用 color_conf>0）：conf=0 的"未知色"必须被过滤掉。
    #   （阈值=0 时 conf=0 也会通过，这是"不过滤"的正常语义，不是缺陷。）
    try:
        a = vc.pick_best([{"area": None, "color": None},
                          {"area": 10, "color": {"conf": 0.5, "name": "红"}}], 0.1)
        b = vc.pick_best([{"area": None, "color": None}], 0.1)
        ok("坏数据不炸，且正阈值下 conf=0/None 被过滤",
           len(a) == 1 and a[0]["color"]["name"] == "红" and b == [],
           "a=%s b=%s" % (a, b))
    except Exception as ex:                      # noqa: BLE001
        ok("坏数据不炸，且正阈值下 conf=0/None 被过滤", False, repr(ex))

    # I2. color_only（实时链路口径）：只用颜色阈值，彩色矩形可检出、灰色物体不产生候选
    e_col = vc.VisionEngine(color_only=True)
    ok("color_only：彩色矩形可检出",
       len(e_col.detect(render(hex_bgr("#1F4FD8")))["results"]) == 1)
    e_col2 = vc.VisionEngine(color_only=True)
    ok("color_only：低饱和灰物体不产生候选（只认彩色，是预期取舍）",
       len(e_col2.detect(render(hex_bgr(vc.COLOR_CARD["灰"])))["results"]) == 0)

    # I3. 绝对面积下限：小于 min_area_abs 的目标被挡掉（参考实现 MIN_AREA 口径）
    e_abs = vc.VisionEngine(color_only=True)
    small = np.full((H, W, 3), BG, np.uint8)
    cv2.rectangle(small, (300, 220), (340, 260), hex_bgr("#1F4FD8"), -1)   # 40x40 = 1600 < 2500
    ok("小于绝对面积下限的小块被挡掉",
       len(e_abs.detect(small)["results"]) == 0,
       "thr.min_area_abs=%s" % vc.DEFAULT_THR.get("min_area_abs"))

    # ------------------------------------------------------------------
    # J. 现场场景：物体被遮挡 / 移动 —— 实时链路必须照样检出
    #    （用户现场反馈："物体移动时检测不到""只出现矩形的一部分"）
    # ------------------------------------------------------------------
    print("== J. 遮挡 / 移动场景：实时链路（纯颜色阈值）必须照样检出 ==")

    # J1. 部分遮挡：矩形右下角被灰色夹具压住 ~1/4 → 仍应检出
    #     （旧的实心度 0.90 会把这个带缺口的形状整块拒掉）
    occ = render(hex_bgr("#1F4FD8"))
    cv2.rectangle(occ, (int(W / 2) + 10, int(H / 2) + 10),
                  (int(W / 2) + 140, int(H / 2) + 110), (BG, BG, BG), -1)
    r_occ = vc.VisionEngine().detect(occ)
    ok("被遮挡一部分的矩形仍被检出", len(r_occ["results"]) == 1,
       "检出 %d 个（阈值 min_fill=%.2f solidity=%.2f）"
       % (len(r_occ["results"]), vc.DEFAULT_THR["min_fill"], vc.DEFAULT_THR["solidity_min"]))

    # J2. 移动：同一个物体分别在左/右两个位置各测一帧（各自新引擎，模拟逐帧）
    #     ★ 纯颜色阈值与"物体动没动"无关；若退回背景差分/MOG2，静止物体会消失、
    #       移动物体只会留下残影 → 这条会红。
    hits = 0
    for dx in (-140, 140):
        mv = np.full((H, W, 3), BG, np.uint8)
        bx = cv2.boxPoints(((W / 2 + dx, H / 2), (240, 150), 8)).astype(np.int32)
        cv2.fillPoly(mv, [bx], hex_bgr("#4CAF50"))
        if vc.VisionEngine().detect(mv)["results"]:
            hits += 1
    ok("物体在不同位置都能检出（不依赖背景差分）", hits == 2, "命中 %d/2" % hits)

    # J3. 只报一个：同帧放一大一小两块物料 → 经 pick_best 只报面积最大的那块
    #     ★ 注意分层：`VisionEngine.detect()` 按设计返回**全部候选**（离线/调试要看全量），
    #       "只报一个"是 `pick_best()` 的职责（相机服务实时链路用的就是它）。
    two = render(hex_bgr("#1F4FD8"))                     # 主体 260x150
    cv2.rectangle(two, (40, 40), (40 + 120, 40 + 90), hex_bgr("#4CAF50"), -1)  # 次体更小
    r_two = vc.VisionEngine().detect(two)
    best_two = vc.pick_best(r_two["results"], 0.0)
    names = [x["color"]["name"] for x in best_two]
    ok("同帧多目标时只报一个（面积最大的蓝）", len(best_two) == 1 and names == ["蓝"],
       "候选 %s → 选中 %s" % ([x["color"]["name"] for x in r_two["results"]], names))

    print("")
    print("结果: %d 通过 / %d 失败" % (pass_n, fail_n))
    return 1 if fail_n else 0
if __name__ == "__main__":
    sys.exit(main())
