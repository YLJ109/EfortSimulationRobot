# -*- coding: utf-8 -*-
"""Pydantic 响应/请求模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class PoseOut(BaseModel):
    j1: float
    j2: float
    j3: float
    j4: float
    j5: float
    j6: float
    tcp_x: float
    tcp_y: float
    tcp_z: float
    source: str = "real"
    simulated: bool = False
    timestamp: str | None = None  # ISO8601


class TcpOut(BaseModel):
    x: float
    y: float
    z: float


class WsMessage(BaseModel):
    """WebSocket 实时推送帧。"""
    type: str = "pose"          # pose | status
    j1: float
    j2: float
    j3: float
    j4: float
    j5: float
    j6: float
    tcp: TcpOut
    simulated: bool = False
    t: str                       # ISO8601 时间戳


class HealthOut(BaseModel):
    status: str = "ok"
    robot: str
    connected: bool
    simulated: bool
    clients: int


class HistoryOut(BaseModel):
    """历史姿态单条（/api/history 响应元素）。"""
    id: int
    timestamp: Optional[str] = None
    j1: float = 0.0
    j2: float = 0.0
    j3: float = 0.0
    j4: float = 0.0
    j5: float = 0.0
    j6: float = 0.0
    tcp_x: float = 0.0
    tcp_y: float = 0.0
    tcp_z: float = 0.0
    source: str = "real"


class MetaOut(BaseModel):
    """机器人元数据（/api/meta 响应）。"""
    robot: str = "ER8-700H"
    serial: str = ""
    reach_mm: float = 0.0
    axes: List[str] = []
    dh_calibration_pending: bool = True
    mounting: str = "floor"
    simulate_mode: str = "auto"
    server_ws_path: str = "/ws/pose"
    dh: List[Dict[str, Any]] = []
    joint_limits: List[Dict[str, Any]] = []
