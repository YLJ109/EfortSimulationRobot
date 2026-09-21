# -*- coding: utf-8 -*-
"""REST 接口: 健康/当前姿态/历史/CSV 导出/元数据。"""
from __future__ import annotations

import csv
import io
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.core.deps import get_db
from app.db.crud import get_recent_poses
from app.schemas.pose import HealthOut, HistoryOut, MetaOut
from app.services.collector import collector
from app.services.hub import hub

router = APIRouter(prefix="/api", tags=["robot"])


@router.get("/health", response_model=HealthOut)
def health():
    return HealthOut(
        robot=get_config().robot_model,
        connected=collector.connected,
        simulated=collector.simulated,
        clients=hub.count(),
    )


@router.post("/reconnect")
def reconnect():
    """手动重连机器人（网线在摄像头/机器人间切换后触发，无需重启服务）。"""
    return collector.reconnect()


@router.get("/pose")
def pose():
    latest = collector.get_latest()
    if latest is None:
        return {"type": "pose", "connected": False, "simulated": True,
                "j1": 0, "j2": 0, "j3": 0, "j4": 0, "j5": 0, "j6": 0,
                "tcp": {"x": 0, "y": 0, "z": 0},
                "t": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}
    return latest


@router.get("/history", response_model=List[HistoryOut])
def history(limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db)):
    rows = get_recent_poses(db, limit)
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() + "Z" if r.timestamp else None,
            "j1": r.j1, "j2": r.j2, "j3": r.j3,
            "j4": r.j4, "j5": r.j5, "j6": r.j6,
            "tcp_x": r.tcp_x, "tcp_y": r.tcp_y, "tcp_z": r.tcp_z,
            "source": r.source,
        }
        for r in rows
    ]


@router.get("/export/csv")
def export_csv(limit: int = Query(2000, ge=1, le=20000), db: Session = Depends(get_db)):
    rows = get_recent_poses(db, limit)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "timestamp", "j1", "j2", "j3", "j4", "j5", "j6",
                "tcp_x", "tcp_y", "tcp_z", "source"])
    for r in rows:
        w.writerow([r.id, r.timestamp.isoformat() if r.timestamp else "",
                    r.j1, r.j2, r.j3, r.j4, r.j5, r.j6,
                    r.tcp_x, r.tcp_y, r.tcp_z, r.source])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pose_history.csv"},
    )


@router.get("/meta", response_model=MetaOut)
def meta():
    cfg = get_config()
    return {
        "robot": cfg.robot_model,
        "serial": cfg.get("robot", "serial", default=""),
        "reach_mm": cfg.get("robot", "reach_mm", default=0),
        "axes": ["J1", "J2", "J3", "J4", "J5", "J6"],
        "dh_calibration_pending": cfg.dh.get("calibration_pending", True),
        "mounting": cfg.get("mounting", "type", default="floor"),
        "simulate_mode": cfg.get("connection", "simulate", default="auto"),
        "server_ws_path": cfg.get("server", "ws_path", default="/ws/pose"),
        "dh": cfg.dh.get("joints", []),
        "joint_limits": cfg.get("joint_limits", default=[]),
    }
