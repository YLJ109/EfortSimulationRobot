# -*- coding: utf-8 -*-
"""用给定的真实图片验证三色识别链路: HSV彩色前景掩膜 -> find_rectangles -> classify_rgb。
用法: python tools/run_test_image.py <图片路径> [min_conf]
不改变引擎任何逻辑, 仅复刻判色主路径, 输出每个目标及是否会被画框/置信度过滤。
"""
import sys, os
import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "camera"))
import vision_color as vc  # noqa: E402

def main():
    path = sys.argv[1]
    min_conf = float(sys.argv[2]) if len(sys.argv) > 2 else 0.35
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        print("无法读取或解码: %s" % path)
        return 1
    h, w = bgr.shape[:2]
    print("测试图尺寸: %dx%d  min_conf=%.2f" % (w, h, min_conf))

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]
    total = float(h * w)
    print("平均饱和度=%.1f/255 平均亮度=%.1f/255 过曝=%.1f%% 暗部=%.1f%%"
          % (float(sat.mean()), float(val.mean()),
             100 * float((val > 250).sum()) / total,
             100 * float((val < 30).sum()) / total))

    mask = ((sat > 80) & (val > 40)).astype(np.uint8) * 255
    nz = int(cv2.countNonZero(mask))
    print("HSV 彩色前景像素=%d (%.3f%%)" % (nz, 100.0 * nz / total))
    if nz == 0:
        print("!! 无高饱和前景 => 本图无鲜艳彩色区域, 判色必然为空")
        return 1
    k = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k), cv2.MORPH_OPEN, k)

    rects = vc.find_rectangles(mask, max_items=12)
    print("find_rectangles 命中矩形候选=%d" % len(rects))
    shown = 0
    kept = 0
    for d in rects:
        box = d["box"]
        sub = np.zeros(mask.shape[:2], np.uint8)
        cv2.fillPoly(sub, [np.int32(box)], 255)
        sub = cv2.bitwise_and(sub, mask)
        dom = vc.dominant_lab(bgr, sub)
        if not dom["ok"]:
            continue
        cr = vc.classify_rgb(dom["lab"])
        conf = cr["conf"]
        verdict = "画框OK" if (cr["name"] not in ("未知",) and conf >= min_conf) else "被滤"
        if cr["name"] != "未知" and conf >= min_conf:
            kept += 1
        if shown < 8:
            x, y, bw, bh = cv2.boundingRect(np.int32(box))
            print("  #%d 框=(%d,%d %dx%d) 颜色=%s 置信度=%.2f 检出=[%s]"
                  % (shown, x, y, bw, bh, cr["name"], conf, verdict))
        shown += 1
    print("最终可画框目标数: %d / %d" % (kept, len(rects)))

if __name__ == "__main__":
    sys.exit(main())