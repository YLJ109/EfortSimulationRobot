# -*- coding: utf-8 -*-
"""用上传截图复现颜色检测管线，定位蓝色矩形在哪一步被滤掉。"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "camera"))
import vision_color as vc  # noqa: E402

PNG = r"c:\Users\FIT\.trae-cn\attachments\6ab3f697c056ddfb097aea71\44a6a7b9-5459-4e1b-99e7-a2f5c63d4ac4_1d73e5b3-a9d8-462b-87df-cff8772a9f86_image.png"
bgr = cv2.imread(PNG, cv2.IMREAD_COLOR)

# 1) 全图定位"蓝"色区域（粗定位相机画面位置）
hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
blu = ((hsv[..., 0] >= 100) & (hsv[..., 0] < 130) & (hsv[..., 1] >= 80) &
       (hsv[..., 2] >= 60)).astype(np.uint8) * 255
blu = cv2.morphologyEx(blu, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
ys, xs = np.nonzero(blu)
print("全图蓝色像素:", int(ys.size))
if ys.size == 0:
    print("!! 整图未检出蓝色像素 —— 先确认画面里到底有没有蓝色")
    sys.exit(0)
x0, x1 = int(xs.min()), int(xs.max())
y0, y1 = int(ys.min()), int(ys.max())
print("蓝色包围框:", (x0, y0, x1 - x0, y1 - y0))

# 2) 取蓝色物体附近一块区域模拟"物体在画面中"的局部
pad = 80
cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
h, w = bgr.shape[:2]
x0c, x1c = max(0, cx - 250), min(w, cx + 250)
y0c, y1c = max(0, cy - 250), min(h, cy + 250)
crop = bgr[y0c:y1c, x0c:x1c].copy()
print("裁剪区域:", crop.shape[1], "x", crop.shape[0])

# 3) 模拟引擎：HSV 彩色掩膜（相机引擎默认 use_hsv_mask=True）
mask = vc.hsv_color_mask(crop)
print("HSV 掩膜有效像素:", int(cv2.countNonZero(mask)))
print(vc.debug_mask_contours(crop, mask) or "  (无连通域)")

# 4) 直接跑 find_rectangles 默认参数（与引擎 detect 一致）
rects = vc.find_rectangles(mask)
print("find_rectangles 候选数:", len(rects))
for r in rects:
    x, y, ww, hh = cv2.boundingRect(r["box"])
    name = vc.hue_color_name(crop[y:y + hh, x:x + ww])
    print("  box=%.0f,%.0f %dx%d fill=%s ar=%s sol=%s 色=%s"
          % (x, y, ww, hh, r["fill"], r["aspect"], r["solidity"], name or "-"))

# 5) 若候选为 0，用 debug 里逐连通域的判定来对照（solidity/四顶点是上传算法没有、我们加的）
print("\n[完]")