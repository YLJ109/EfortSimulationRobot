# -*- coding: utf-8 -*-
"""客观排查：抓取相机当前帧，程序化统计画面里的高饱和(彩色)区域与过曝情况，
不展示图片，仅输出数据，用于判断"物体是否在 USB 相机可视范围内"。
用法: python tools/probe_vision.py [index]   index=opencv 设备索引，默认0
"""
import sys, io, urllib.request
import numpy as np
import cv2

BASE = "http://127.0.0.1:8100"

def grab(url):
    with urllib.request.urlopen(url, timeout=6) as r:
        return np.frombuffer(r.read(), np.uint8)

def main():
    snap = grab(BASE + "/snapshot")
    img = cv2.imdecode(snap, cv2.IMREAD_COLOR)
    if img is None:
        print("无法解码快照")
        return
    h, w = img.shape[:2]
    print("画面尺寸: %dx%d" % (w, h))
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat, val, hue = hsv[..., 1], hsv[..., 2], hsv[..., 0]
    total = float(h * w)
    sat_mean = float(sat.mean())
    val_mean = float(val.mean())
    print("平均饱和度=%.1f / 255, 平均亮度=%.1f / 255" % (sat_mean, val_mean))
    over = int((val > 250).sum())
    dark = int((val < 30).sum())
    print("过曝像素=%.1f%%, 暗部(<30)=%.1f%%" % (100.0 * over / total, 100.0 * dark / total))
    # 模拟 HSV 彩色前景掩膜(与引擎同阈值)
    color_mask = ((sat > 80) & (val > 40)).astype(np.uint8) * 255
    cpy = int(cv2.countNonZero(color_mask))
    print("高饱和彩色前景像素(饱和>80&亮>40)=%d (%.3f%%)" % (cpy, 100.0 * cpy / total))
    if cpy > 0:
        k = np.ones((5, 5), np.uint8)
        m = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, k)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        print("掩膜连通域数: %d" % len(cnts))
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        for i, c in enumerate(cnts[:5]):
            area = cv2.contourArea(c)
            box = cv2.boxPoints(cv2.minAreaRect(c))
            rx, ry, rw, rh = cv2.boundingRect(np.int32(box))
            fill = area / float(max(1, rw * rh))
            ar = (rw / float(rh)) if rh else 0
            if ar < 1:
                ar = 1.0 / ar
            # 该区域主色
            sub = np.zeros(img.shape[:2], np.uint8)
            cv2.fillPoly(sub, [np.int32(box)], 255)
            sub = cv2.bitwise_and(sub, m)
            px = img[sub > 0]
            bgr = px.mean(axis=0) if len(px) else (0, 0, 0)
            print("  #%d area=%d BGR=(%d,%d,%d) fill=%.2f 长宽比=%.2f"
                  % (i, int(area), int(bgr[0]), int(bgr[1]), int(bgr[2]), fill, ar))
    else:
        print("!! 画面无高饱和彩色前景 => 彩色物体不在当前相机视野内")

if __name__ == "__main__":
    main()