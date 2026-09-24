# -*- coding: utf-8 -*-
"""一次性硬排查(fix后验证)：在真实测试图(工业蓝偏青面板)上
  跑项目 vision_color.find_rectangles 管线 + _hue_vote 判色 + 置信度，
  并画框保存证明图。对比用户能工作的算法。
"""
from __future__ import annotations
import sys, os
import cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../camera")
import vision_color as vc

SRC = r"c:\Users\FIT\.trae-cn\attachments\6ab3f697c056ddfb097aea71\bbda9a4b-dc02-4bad-b6d1-df1037872ae9_a40da01b-6418-4785-a0d6-0e810590efeb_default.jfif"
frame = cv2.imread(SRC)
print("frame", frame.shape)

# 与 _mask(use_hsv) 一致的彩色前景掩膜
blurred = cv2.GaussianBlur(frame, (5, 5), 0)
hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
pm = ((hsv[..., 1] > 80) & (hsv[..., 2] > 40)).astype(np.uint8) * 255
k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
mask = cv2.morphologyEx(cv2.morphologyEx(pm, cv2.MORPH_CLOSE, k), cv2.MORPH_OPEN, k)

rects = vc.find_rectangles(mask)
print("find_rectangles 入选 %d 个候选:" % len(rects))
for r in rects:
    box = r["box"]; xs = box[:, 0]; ys = box[:, 1]
    x0, y0 = int(xs.min()), int(ys.min())
    x1, y1 = int(xs.max()), int(ys.max())
    col = vc.hue_color_name(frame[y0:y1 + 1, x0:x1 + 1]) or "-"
    conf = round(float((vc._hue_vote(frame[y0:y1 + 1, x0:x1 + 1])[1])
                       * (0.5 + 0.5 * min(1.0, r["fill"]))), 3)
    print("  box=(%d,%d,%d,%d) aspect=%.2f fill=%.3f sol=%.3f 判色=%s conf=%.3f"
          % (x0, y0, x1 - x0, y1 - y0, r["aspect"], r["fill"], r["solidity"], col, conf))
    cv2.polylines(frame, [box.astype(np.int32)], True, (0, 230, 118), 8)
    cv2.putText(frame, "%s %.0f%%" % (col, conf * 100), (x0, max(0, y0 - 12)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 230, 118), 5)

out = r"d:\EFORT_Projects\EFORT_Web_Monitoring\tools\_fix_proof.png"
cv2.imwrite(out, frame)
print("已保存标注截图:", out)