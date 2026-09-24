# -*- coding: utf-8 -*-
"""REST 接口: 健康/当前姿态/历史/CSV 导出/元数据。"""
from __future__ import annotations

import csv
import io
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.api.auth import require_control
from app.core.config import get_config
from app.core.deps import get_db
from app.db.crud import get_recent_poses
from app.schemas.pose import HealthOut, HistoryOut, MetaOut
from app.services.collector import collector, feed_run_mode
from app.services.hub import hub
from app.services.motion import motion, real_write_enabled
from app.services.rc_ready import ReadinessService

router = APIRouter(prefix="/api", tags=["robot"])
_ready = ReadinessService()


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


@router.get("/rc-status")
def rc_status():
    """控制器寄存器快照（只读，不写任何寄存器）。

    返回状态位解码（模式/伺服/报警/急停/程序）、报警码、当前程序号、
    点动触发/完成位、以及"真实下发是否已开启"与点动服务程序号。
    供前端「真机链路就绪」卡展示与排障。
    """
    snap, err = motion.modbus.rc_snapshot()
    cfg = get_config()
    prog_no = 0
    try:
        prog_no = int(cfg.get("motion", "jog", "service_program", default=0) or 0)
    except Exception:
        prog_no = 0
    if snap is None:
        return {"ok": False, "error": err or "读不到控制器",
                "real_enabled": real_write_enabled(), "service_program": prog_no}
    snap["ok"] = True
    snap["real_enabled"] = real_write_enabled()
    snap["service_program"] = prog_no
    # 就绪态一句话判定（前端直接显示）
    b = snap["bits"]
    snap["ready"] = bool(b["servo"] and b["prog_loaded"] and b["run"]
                         and (b["auto"] or b["remote"]) and not b["alarm"] and not b["estop"])
    # ★ 实测档位喂给 runmode（与采集循环同一条路）：用户手动刷新真机链路时，
    #   底栏「示教器」灯也能立刻反映旋钮实际位置，不必等下一个 4s 采集周期。
    feed_run_mode(snap.get("mode"), snap.get("status_word"))
    return snap


class ReadyIn(BaseModel):
    prog: Optional[int] = None   # 缺省用 config motion.jog.service_program


@router.post("/ready")
def ready(body: Optional[ReadyIn] = None, request: Request = None,
          tok: str = Depends(require_control)):
    """一键就绪：清报警 → 伺服上电 → 加载点动服务程序 → 运行挂起于 WAIT。

    ★ 本身就是真机寄存器写操作：需控制令牌 + 双确认总闸（EFORT_REAL_MOTION=1
      且 motion.real_write=true）。总闸没开时明确拒绝并解释怎么开。
    """
    ip = request.client.host if (request and request.client) else ""
    from app.services.events import emit as emit_event
    r = _ready.ready(prog=(body.prog if body else None),
                     on_step=lambda s, m: None)
    emit_event("control", "info" if r.get("ok") else "warn", "control.ready_request",
               "一键就绪：%s" % ("成功" if r.get("ok") else (r.get("error") or "失败")),
               {"steps": r.get("steps", [])}, actor="web", ip=ip)
    return r


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
