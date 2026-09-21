# -*- coding: utf-8 -*-
"""
EFORT 视觉检测服务 — 海康机器人 GigE 工业相机 + YOLO 目标检测。

独立进程运行 (复用 EFORT_Camera_Python_OpenCv 的 venv, 内含 torch/ultralytics/MVS SDK):
    <参考项目>/venv/Scripts/python.exe camera/camera_service.py

仅用 Python 标准库 http.server 提供:
    GET  /stream          MJPEG 实时流(可含检测框)
    GET  /snapshot        当前单帧 JPEG
    POST /snapshot-save   保存当前帧到 camera/captures/
    GET  /status          状态 JSON
    GET  /models          可选模型列表
    POST /open, /close    打开/关闭相机 (异步, 立即返回, 前端轮询 /status)
    POST /reconnect       手动重连相机 (网口切换后, 无需重启服务)
    POST /config          更新 AI 配置 (enabled/model/conf/imgsz)
    POST /unload          卸载 YOLO 模型释放内存 (torch 加载后常驻, 可达数 GB)
所有响应带 CORS 头, 供 :8000 上的网页直接调用。

性能设计:
    - 抓帧线程: 抓全分辨率 -> 立即缩放到 STREAM_MAX_W -> 只保存小图(省内存/CPU)
    - 编码线程(单例): 只在有 MJPEG 客户端时, 把 [小图+检测框] 编码成 JPEG 一次
    - 多个客户端共享同一份 latest_jpeg, 不重复编码
    - 检测线程: 用同一张小图推理, 检测框坐标与显示图一致
"""
from __future__ import annotations

import gc
import io
import json
import os
import sys
import threading
import time
from ctypes import *
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- MVS 运行时 DLL (海康机器人相机 SDK) ----
# 默认路径是 MVS 客户端标准安装位置; 装到别处时用 EFORT_MVS_RUNTIME_DIR 覆盖。
MVS_DLL_DIR = os.environ.get(
    "EFORT_MVS_RUNTIME_DIR",
    r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
)
if os.path.isdir(MVS_DLL_DIR):
    os.environ["PATH"] = MVS_DLL_DIR + os.pathsep + os.environ.get("PATH", "")

# ---- 复用参考项目的 MVS Python 封装 ----
# MvCameraControl_class.py 不在 PyPI 上, 随 MVS 客户端/厂商样例包提供。
# 默认指向本机已存在的参考项目; 换机器时用 EFORT_MVS_SDK_DIR 覆盖。
REF_SDK = os.environ.get(
    "EFORT_MVS_SDK_DIR",
    r"D:\EFORT_Projects\EFORT_Camera_Python_OpenCv\src\sdk\MvImport",
)
if os.path.isdir(REF_SDK):
    sys.path.append(REF_SDK)

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from MvCameraControl_class import (
        MvCamera, MV_CC_DEVICE_INFO_LIST, MV_CC_DEVICE_INFO, MV_FRAME_OUT,
        MV_CC_PIXEL_CONVERT_PARAM, MVCC_ENUMENTRY,
        MV_GIGE_DEVICE, MV_USB_DEVICE, MV_GENTL_GIGE_DEVICE,
        MV_ACCESS_Exclusive, MV_TRIGGER_MODE_OFF,
        PixelType_Gvsp_RGB8_Packed,
    )
    _SDK_OK = True
    _SDK_ERR = ""
except Exception as e:
    _SDK_OK = False
    _SDK_ERR = str(e)

PORT = int(os.environ.get("CAMERA_PORT", "8100"))
MODELS_DIR = os.path.join(HERE, "models")
CAPTURE_DIR = os.path.join(HERE, "captures")
STREAM_MAX_W = 1280          # 抓帧后统一缩放到的宽度(检测与显示共用)
JPEG_QUALITY = 78
STREAM_FPS = 20              # 编码目标帧率上限
os.makedirs(CAPTURE_DIR, exist_ok=True)

CN_NAMES = {
    "person": "人", "bicycle": "自行车", "car": "汽车", "motorcycle": "摩托车",
    "airplane": "飞机", "bus": "公交车", "train": "火车", "truck": "卡车",
    "boat": "船", "traffic light": "红绿灯", "fire hydrant": "消防栓",
    "stop sign": "停车标志", "parking meter": "停车计时器", "bench": "长椅",
    "bird": "鸟", "cat": "猫", "dog": "狗", "horse": "马", "sheep": "羊",
    "cow": "牛", "elephant": "大象", "bear": "熊", "zebra": "斑马",
    "giraffe": "长颈鹿", "backpack": "背包", "umbrella": "雨伞",
    "handbag": "手提包", "tie": "领带", "suitcase": "行李箱", "frisbee": "飞盘",
    "skis": "滑雪板", "snowboard": "滑雪单板", "sports ball": "运动球",
    "kite": "风筝", "baseball bat": "棒球棒", "baseball glove": "棒球手套",
    "skateboard": "滑板", "surfboard": "冲浪板", "tennis racket": "网球拍",
    "bottle": "瓶子", "wine glass": "酒杯", "cup": "杯子", "fork": "叉子",
    "knife": "刀", "spoon": "勺子", "bowl": "碗", "banana": "香蕉",
    "apple": "苹果", "sandwich": "三明治", "orange": "橙子", "broccoli": "西兰花",
    "carrot": "胡萝卜", "hot dog": "热狗", "pizza": "披萨", "donut": "甜甜圈",
    "cake": "蛋糕", "chair": "椅子", "couch": "沙发", "potted plant": "盆栽",
    "bed": "床", "dining table": "餐桌", "toilet": "马桶", "tv": "电视",
    "laptop": "笔记本", "mouse": "鼠标", "remote": "遥控器", "keyboard": "键盘",
    "cell phone": "手机", "microwave": "微波炉", "oven": "烤箱",
    "toaster": "烤面包机", "sink": "水槽", "refrigerator": "冰箱", "book": "书",
    "clock": "时钟", "vase": "花瓶", "scissors": "剪刀", "teddy bear": "泰迪熊",
    "hair drier": "吹风机", "toothbrush": "牙刷",
}

_FONT_CACHE: dict = {}


def _font(size: int):
    size = max(12, int(size))
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    try:
        f = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size)
    except Exception:
        f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


def _decoding_char(c_arr):
    b = memoryview(c_arr).tobytes()
    i = b.find(b"\x00")
    if i != -1:
        b = b[:i]
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode("latin-1", errors="replace")


def _ip_to_str(n):
    return "%d.%d.%d.%d" % ((n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)


def _trim_working_set() -> bool:
    """Windows: 让系统回收本进程工作集, 把物理内存归还 OS。

    纯 Python/C 扩展(torch)释放对象后, 内存分配器往只缓存不归还系统,
    任务管理器里仍显示高占用; EmptyWorkingSet 可把工作集裁剪到最小,
    显著降低任务管理器显示的内存(虚拟内存不变, 但物理内存归还)。
    """
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32.dll")
        psapi = ctypes.WinDLL("psapi.dll")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        psapi.EmptyWorkingSet(kernel32.GetCurrentProcess())
        return True
    except Exception:
        return False


def _enum_devices():
    device_list = MV_CC_DEVICE_INFO_LIST()
    tlayer = MV_GIGE_DEVICE | MV_USB_DEVICE | MV_GENTL_GIGE_DEVICE
    ret = MvCamera.MV_CC_EnumDevices(tlayer, device_list)
    if ret != 0:
        raise RuntimeError("EnumDevices failed 0x%x" % ret)
    found = []
    for i in range(device_list.nDeviceNum):
        info = cast(device_list.pDeviceInfo[i], POINTER(MV_CC_DEVICE_INFO)).contents
        if info.nTLayerType in (MV_GIGE_DEVICE, MV_GENTL_GIGE_DEVICE):
            g = info.SpecialInfo.stGigEInfo
            found.append((i, "GigE", _decoding_char(g.chModelName),
                          _decoding_char(g.chSerialNumber), _ip_to_str(g.nCurrentIp)))
        elif info.nTLayerType == MV_USB_DEVICE:
            u = info.SpecialInfo.stUsb3VInfo
            found.append((i, "USB3", _decoding_char(u.chModelName),
                          _decoding_char(u.chSerialNumber), "-"))
    return device_list, found


def _open_camera(device_list, idx):
    cam = MvCamera()
    info = cast(device_list.pDeviceInfo[idx], POINTER(MV_CC_DEVICE_INFO)).contents
    ret = cam.MV_CC_CreateHandle(info)
    if ret != 0:
        raise RuntimeError("CreateHandle failed 0x%x" % ret)
    ret = cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
    if ret != 0:
        cam.MV_CC_DestroyHandle()
        raise RuntimeError("OpenDevice failed 0x%x (相机可能被 MVS/其他程序占用)" % ret)
    if info.nTLayerType in (MV_GIGE_DEVICE, MV_GENTL_GIGE_DEVICE):
        try:
            pkt = cam.MV_CC_GetOptimalPacketSize()
            if pkt and pkt > 0:
                cam.MV_CC_SetIntValue("GevSCPSPacketSize", pkt)
        except Exception:
            pass
    cam.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
    return cam


def _frame_to_rgb(cam, frame):
    w = frame.stFrameInfo.nWidth
    h = frame.stFrameInfo.nHeight
    dst_size = w * h * 3 + 4096
    buf = (c_ubyte * dst_size)()
    cp = MV_CC_PIXEL_CONVERT_PARAM()
    cp.nWidth = w
    cp.nHeight = h
    cp.enSrcPixelType = frame.stFrameInfo.enPixelType
    cp.pSrcData = frame.pBufAddr
    cp.nSrcDataLen = frame.stFrameInfo.nFrameLen
    cp.enDstPixelType = PixelType_Gvsp_RGB8_Packed
    cp.pDstBuffer = cast(buf, POINTER(c_ubyte))
    cp.nDstBufferSize = dst_size
    if cam.MV_CC_ConvertPixelType(cp) != 0:
        return None
    n = cp.nDstLen if cp.nDstLen else w * h * 3
    arr = np.frombuffer(buf, dtype=np.uint8, count=min(n, dst_size))
    if arr.size < w * h * 3:
        return None
    return arr[:w * h * 3].reshape(h, w, 3)


# ============================ 服务主体 ============================
class CameraService:
    def __init__(self):
        self.lock = threading.RLock()
        self.cam = None
        self.sdk_ready = False
        self.running = False
        self.opening = False
        self.closing = False
        self.latest_frame = None        # PIL RGB, 已缩放到 STREAM_MAX_W
        self.latest_jpeg = None         # 编码线程产出, 供所有客户端共享
        self.latest_dets = []           # [(xyxy, raw_name, conf)] 与 latest_frame 同尺寸
        self.frame_count = 0
        self.fps = 0.0
        self.info = {}
        self.error = "" if _SDK_OK else ("SDK 导入失败: " + _SDK_ERR)
        self.stream_clients = 0
        # AI 配置
        self.yolo_enabled = False
        self.yolo_name = "yolo26n.pt"
        self.yolo_model = None
        self.yolo_loading = False
        self.conf = 0.25
        self.imgsz = 640
        self.det_ms = 0.0
        self.det_count = 0
        self.opened_at = 0.0
        self._grab_thread = None
        self._det_thread = None
        self._enc_thread = None

    # ---------- 状态 ----------
    def status(self):
        with self.lock:
            return {
                "sdk_ok": _SDK_OK,
                "error": self.error,
                "opened": self.cam is not None,
                "opening": self.opening,
                "closing": self.closing,
                "running": self.running,
                "device": self.info,
                "fps": round(self.fps, 1),
                "frames": self.frame_count,
                "resolution": ("%dx%d" % (self.latest_frame.width, self.latest_frame.height))
                if self.latest_frame else "-",
                "ai_enabled": self.yolo_enabled,
                "ai_model": self.yolo_name,
                "ai_loaded": self.yolo_model is not None,
                "ai_loading": self.yolo_loading,
                "conf": self.conf,
                "imgsz": self.imgsz,
                "det_count": self.det_count,
                "det_ms": round(self.det_ms * 1000, 1),
                "clients": self.stream_clients,
                "models": self.list_models(),
                "port": PORT,
            }

    def list_models(self):
        if not os.path.isdir(MODELS_DIR):
            return []
        return sorted([f for f in os.listdir(MODELS_DIR) if f.endswith(".pt")])

    # ---------- 相机 (异步) ----------
    def open_async(self):
        with self.lock:
            if self.cam is not None:
                return True, "已打开"
            if self.opening:
                return True, "正在打开..."
            self.opening = True
            self.error = ""
        threading.Thread(target=self._do_open, name="open", daemon=True).start()
        return True, "正在打开..."

    def _do_open(self):
        try:
            if not _SDK_OK:
                raise RuntimeError("SDK 不可用: " + _SDK_ERR)
            if not self.sdk_ready:
                MvCamera.MV_CC_Initialize()
                self.sdk_ready = True
            device_list, found = _enum_devices()
            if not found:
                raise RuntimeError("未找到相机(检查网线/网卡 192.168.1.x)")
            cam = _open_camera(device_list, found[0][0])
            ret = cam.MV_CC_StartGrabbing()
            if ret != 0:
                cam.MV_CC_CloseDevice()
                cam.MV_CC_DestroyHandle()
                raise RuntimeError("开始采集失败 0x%x" % ret)
            with self.lock:
                self.cam = cam
                self.info = {"layer": found[0][1], "model": found[0][2],
                             "serial": found[0][3], "ip": found[0][4]}
                self.running = True
                self.error = ""
                self.opened_at = time.time()
            self._start_threads()
        except Exception as e:
            with self.lock:
                self.error = str(e)
        finally:
            with self.lock:
                self.opening = False

    def _start_threads(self):
        if self._grab_thread is None or not self._grab_thread.is_alive():
            self._grab_thread = threading.Thread(target=self._grab_loop, name="grab", daemon=True)
            self._grab_thread.start()
        if self._enc_thread is None or not self._enc_thread.is_alive():
            self._enc_thread = threading.Thread(target=self._encode_loop, name="encode", daemon=True)
            self._enc_thread.start()

    def close_async(self):
        with self.lock:
            if self.cam is None and not self.opening:
                # 相机本就没打开: 但检测模型可能仍常驻(占数 GB) -> 一并卸载释放内存
                if self.yolo_model is not None:
                    threading.Thread(target=self.unload_model, name="unload", daemon=True).start()
                    return True, "已关闭(并释放检测模型)"
                return True, "已关闭"
            self.closing = True
        threading.Thread(target=self._do_close, name="close", daemon=True).start()
        return True, "正在关闭..."

    def _do_close(self, unload=True):
        try:
            with self.lock:
                # 状态先复位: 相机立即视为已关闭, 后续 MVS SDK 清理(StopGrabbing/
                # CloseDevice 在相机异常/拔线时可能长时间阻塞)不再拖住前端 UI。
                self.running = False
                cam = self.cam
                self.cam = None
                self.latest_frame = None
                self.latest_jpeg = None
                self.latest_dets = []
                self.det_count = 0
                self.fps = 0.0
                self.closing = False
            if cam is not None:
                try:
                    cam.MV_CC_StopGrabbing()
                    cam.MV_CC_CloseDevice()
                    cam.MV_CC_DestroyHandle()
                except Exception:
                    pass
            # 等抓帧线程退出(它可能正卡在 1s 超时的 GetImageBuffer)
            t = self._grab_thread
            if t is not None and t.is_alive():
                t.join(timeout=2.0)
            # 主动关闭相机时一并卸载检测模型, 释放 torch 常驻内存(约数 GB)
            # 重连(reconnect)时传 unload=False, 保留模型以便切换回来直接检测
            if unload and self.yolo_model is not None:
                self.unload_model()
        except Exception as e:
            with self.lock:
                self.error = "关闭异常: %s" % e
        finally:
            with self.lock:
                self.closing = False

    def reconnect_async(self):
        """手动重连（网线在摄像头/机器人间切换后触发）：先关再开，无需重启服务。"""
        with self.lock:
            if self.opening or self.closing:
                return False, "正在切换中, 请稍候"
            self.closing = True
        threading.Thread(target=self._do_reconnect, name="reconnect", daemon=True).start()
        return True, "正在重连..."

    def _do_reconnect(self):
        self._do_close(unload=False)   # 重连不卸载模型(切换回来可直接继续检测)
        with self.lock:
            self.opening = True
            self.error = ""
        self._do_open()           # 重新枚举设备并打开（finally 会重置 opening）

    # ---------- 抓帧线程 ----------
    def _grab_loop(self):
        last = time.time()
        counter = 0
        while self.running:
            # 局部持有一份句柄: 避免与 _do_close 竞态(self.cam 可能被中途置 None)
            cam = self.cam
            if cam is None:
                break
            frame = MV_FRAME_OUT()
            memset(byref(frame), 0, sizeof(frame))
            try:
                ret = cam.MV_CC_GetImageBuffer(frame, 500)
                if ret != 0 or not frame.pBufAddr:
                    continue
                rgb = _frame_to_rgb(cam, frame)
                cam.MV_CC_FreeImageBuffer(frame)
            except Exception:
                # 相机在抓帧过程中被关闭/拔出(句柄失效): 安全退出抓帧循环
                break
            if rgb is None:
                continue
            img = Image.fromarray(rgb, "RGB")
            if img.width > STREAM_MAX_W:      # 立即降分辨率, 后续都用小图
                h = int(img.height * STREAM_MAX_W / img.width)
                img = img.resize((STREAM_MAX_W, h), Image.Resampling.BILINEAR)
            with self.lock:
                self.latest_frame = img
                self.frame_count += 1
            counter += 1
            now = time.time()
            if now - last >= 1.0:
                with self.lock:
                    self.fps = counter / (now - last)
                counter = 0
                last = now

    # ---------- 编码线程 (单例, 多客户端共享) ----------
    def _encode_loop(self):
        target_dt = 1.0 / STREAM_FPS
        while self.running:
            t0 = time.time()
            if self.stream_clients > 0:
                jpg = self.render_jpeg()
                if jpg is not None:
                    with self.lock:
                        self.latest_jpeg = jpg
            dt = time.time() - t0
            time.sleep(max(0.005, target_dt - dt))

    def render_jpeg(self, quality=JPEG_QUALITY):
        """用最新小图 + 检测框编码一帧 JPEG。"""
        with self.lock:
            if self.latest_frame is None:
                return None
            img = self.latest_frame
            dets = list(self.latest_dets) if self.yolo_enabled else []
        if dets:
            img = img.copy()
            img = self._draw(img, dets)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def get_jpeg(self):
        with self.lock:
            return self.latest_jpeg

    # ---------- AI ----------
    def set_config(self, enabled=None, model=None, conf=None, imgsz=None):
        if conf is not None:
            self.conf = max(0.05, min(0.95, float(conf)))
        if imgsz is not None:
            self.imgsz = max(320, min(1280, int(imgsz)))
        need_load = False
        if model is not None and model != self.yolo_name:
            self.yolo_name = model
            self.yolo_model = None
            need_load = True
        if enabled is not None:
            self.yolo_enabled = bool(enabled)
            # 取消勾选"启用检测" -> 卸载模型释放 torch 常驻内存(不用就不占)
            if not self.yolo_enabled and self.yolo_model is not None:
                self.unload_model()
                return self.status()
        # 启用时: 模型未加载 -> 加载; 已加载但检测线程已退出 -> 重启线程
        if self.yolo_enabled:
            if self.yolo_model is None and not self.yolo_loading:
                need_load = True
            elif self.yolo_model is not None and (self._det_thread is None or not self._det_thread.is_alive()):
                need_load = True
        if need_load:
            threading.Thread(target=self._load_yolo, args=(self.yolo_name,), daemon=True).start()
        return self.status()

    def _load_yolo(self, name):
        self.yolo_loading = True
        try:
            if self.yolo_model is None:
                from ultralytics import YOLO
                path = name if os.path.isabs(name) else os.path.join(MODELS_DIR, name)
                self.yolo_model = YOLO(path)
            self.error = ""
            if self._det_thread is None or not self._det_thread.is_alive():
                self._det_thread = threading.Thread(target=self._detect_loop, name="detect", daemon=True)
                self._det_thread.start()
        except Exception as e:
            self.error = "模型加载失败: %s" % e
        finally:
            self.yolo_loading = False

    def unload_model(self):
        """卸载 YOLO 检测模型并尽量回收内存。

        torch(PyTorch) 一旦加载即常驻, 占用可达数 GB; 本方法释放模型对象 +
        触发 GC + 清空 torch 缓存, 把不用检测时的内存尽可能还回系统。
        """
        if self.yolo_loading:
            return False, "模型正在加载中, 请稍候"
        with self.lock:
            self.yolo_enabled = False
            self.yolo_model = None
            self.latest_dets = []
            self.det_count = 0
            self.det_ms = 0.0
        # 等检测线程退出
        t = self._det_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._det_thread = None
        # 尽量回收: gc + torch 缓存 + Windows 工作集裁剪
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass
        gc.collect()
        _trim_working_set()
        return True, "已卸载检测模型并回收内存"

    def _detect_loop(self):
        n = 0
        while self.yolo_enabled and self.yolo_model is not None:
            # 局部持有一份模型引用: 避免与 unload_model 竞态(中途被置 None)
            model = self.yolo_model
            if model is None:
                break
            with self.lock:
                frame = self.latest_frame.copy() if self.latest_frame is not None else None
            if frame is None:
                time.sleep(0.1)
                continue
            try:
                t0 = time.time()
                arr = np.asarray(frame)
                results = model.predict(arr, conf=self.conf, imgsz=self.imgsz, verbose=False)
                self.det_ms = time.time() - t0
                dets = []
                r0 = results[0] if results else None
                if r0 is not None and r0.boxes is not None:
                    names = r0.names
                    for b in r0.boxes:
                        xyxy = [float(v) for v in b.xyxy[0].tolist()]
                        cls = int(b.cls[0])
                        raw = names[cls] if cls in names else str(cls)
                        dets.append((xyxy, raw, float(b.conf[0])))
                with self.lock:
                    self.latest_dets = dets
                    self.det_count = len(dets)
                # 每 100 帧裁剪一次工作集: torch 推理的内存分配器只缓存不归还,
                # 长时间运行会让物理内存持续上涨, 定期归还系统(不阻塞检测)
                n += 1
                if n % 100 == 0:
                    _trim_working_set()
            except Exception as e:
                with self.lock:
                    self.error = "检测异常: %s" % str(e)[:120]
                time.sleep(0.5)

    def _draw(self, img, dets):
        draw = ImageDraw.Draw(img)
        fsize = max(16, int(min(img.width, img.height) * 0.024))
        font = _font(fsize)
        lw = max(2, int(fsize / 9))
        for xyxy, raw, conf in dets:
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            x1, y1 = max(0, x1), max(0, y1)
            cn = CN_NAMES.get(raw, raw)
            label = "%s(%s) %.2f" % (cn, raw, conf)
            draw.rectangle([x1, y1, x2, y2], outline=(0, 255, 90), width=lw)
            tw = draw.textlength(label, font=font)
            th = fsize + 6
            draw.rectangle([x1, max(0, y1 - th), x1 + int(tw) + 6, y1], fill=(0, 255, 90))
            draw.text((x1 + 3, max(0, y1 - th) + 3), label, fill=(0, 0, 0), font=font)
        return img

    def save_snapshot(self):
        with self.lock:
            if self.latest_frame is None:
                return None
            img = self.latest_frame.copy()
            dets = list(self.latest_dets) if self.yolo_enabled else []
        name = "capture_%s.jpg" % time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(CAPTURE_DIR, name)
        out = self._draw(img, dets) if dets else img
        out.save(path, quality=92)
        return path


SERVICE = CameraService()


# ============================ HTTP ============================
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def handle(self):
        """客户端在请求完成前断开(刷新页面 / 中断 MJPEG 流)会抛
        ConnectionAbortedError(10053)/ConnectionResetError 等, 这里静默忽略,
        避免 socketserver.handle_error 打印满屏 traceback。"""
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/stream":
            return self._stream()
        if path == "/snapshot":
            jpg = SERVICE.render_jpeg(quality=90)   # 按需编码一张高质量单帧
            if jpg is None:
                return self._json({"ok": False, "error": "无画面(相机未开启)"}, 503)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(jpg)
            return
        if path == "/status":
            return self._json(SERVICE.status())
        if path == "/models":
            return self._json({"models": SERVICE.list_models()})
        return self._json({"ok": False, "error": "未知路径"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/open":
            ok, msg = SERVICE.open_async()
            return self._json({"ok": ok, "message": msg, "status": SERVICE.status()})
        if path == "/close":
            ok, msg = SERVICE.close_async()
            return self._json({"ok": ok, "message": msg, "status": SERVICE.status()})
        if path == "/config":
            st = SERVICE.set_config(
                enabled=body.get("enabled"), model=body.get("model"),
                conf=body.get("conf"), imgsz=body.get("imgsz"))
            return self._json({"ok": True, "status": st})
        if path == "/snapshot-save":
            p = SERVICE.save_snapshot()
            return self._json({"ok": bool(p), "path": p})
        if path == "/reconnect":
            ok, msg = SERVICE.reconnect_async()
            return self._json({"ok": ok, "message": msg, "status": SERVICE.status()})
        if path == "/unload":
            ok, msg = SERVICE.unload_model()
            return self._json({"ok": ok, "message": msg, "status": SERVICE.status()})
        return self._json({"ok": False, "error": "未知路径"}, 404)

    def _stream(self):
        """MJPEG: 只发送编码线程产出的 latest_jpeg, 不在此处编码。"""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        with SERVICE.lock:
            SERVICE.stream_clients += 1
        last = None
        try:
            while SERVICE.running:
                jpg = SERVICE.get_jpeg()
                if jpg is not None and jpg is not last:
                    last = jpg
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(("Content-Length: %d\r\n\r\n" % len(jpg)).encode())
                    self.wfile.write(jpg)
                    self.wfile.write(b"\r\n")
                else:
                    time.sleep(0.01)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with SERVICE.lock:
                SERVICE.stream_clients -= 1


def main():
    print("=" * 56)
    print("  EFORT 视觉检测服务  (海康 GigE + YOLO)")
    print("  http://127.0.0.1:%d/stream" % PORT)
    print("  SDK: %s" % ("OK" if _SDK_OK else _SDK_ERR))
    print("  模型目录: %s" % MODELS_DIR)
    print("  MVS 封装: %s%s" % (REF_SDK, "" if os.path.isdir(REF_SDK) else "  (目录不存在!)"))
    print("=" * 56)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SERVICE._do_close()


if __name__ == "__main__":
    main()
