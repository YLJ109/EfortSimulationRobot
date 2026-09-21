# -*- coding: utf-8 -*-
"""安全围栏配置与报警事件 API。

- 配置持久化在 config/safety.json（与 robot.yaml 同级，便于手工编辑/备份）
- 报警事件写入 SQLite，供前端时间线展示
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.exceptions import AppError
from app.core.safety_config import (
    SafetyConfigError,
    default_safety,
    load_safety,
    reset_safety,
    save_safety,
    validate_safety,
)
from app.db.crud import clear_safety_events, insert_safety_event, list_safety_events

router = APIRouter(prefix="/api", tags=["safety"])


class SafetyIn(BaseModel):
    """整体配置（直接透传，后端做结构校验）。"""

    config: Dict[str, Any]


class EventIn(BaseModel):
    zone_id: str = ""
    zone_name: str = ""
    state: str = Field(..., description="danger | hit | clear")
    clearance: float = 0.0
    ratio: float = 0.0


class EventOut(BaseModel):
    id: int
    timestamp: str
    zone_id: str = ""
    zone_name: str = ""
    state: str = ""
    clearance: float = 0.0
    ratio: float = 0.0


def _iso(dt) -> str:
    try:
        return dt.isoformat()
    except Exception:
        return ""


@router.get("/safety")
def api_get_safety():
    """读取当前安全围栏配置。"""
    return load_safety()


@router.put("/safety")
def api_put_safety(body: SafetyIn):
    """保存配置（后端强校验，非法返回 400 并带具体字段）。"""
    try:
        cfg = save_safety(body.config)
    except SafetyConfigError as e:
        raise AppError(f"配置校验失败: {e}", 400, "SAFETY_CONFIG_INVALID", str(e))
    return {"ok": True, "config": cfg}


@router.post("/safety/reset")
def api_reset_safety():
    """恢复出厂默认配置。"""
    return {"ok": True, "config": reset_safety()}


@router.get("/safety/default")
def api_default_safety():
    """返回出厂默认（供前端"重置"预览/撤销用，不落盘）。"""
    return default_safety()


@router.post("/safety/validate")
def api_validate_safety(body: SafetyIn):
    """只校验不落盘（前端可先试算）。"""
    try:
        cfg = validate_safety(body.config)
    except SafetyConfigError as e:
        raise AppError(f"配置校验失败: {e}", 400, "SAFETY_CONFIG_INVALID", str(e))
    return {"ok": True, "config": cfg}


@router.get("/safety/events")
def api_list_events(limit: int = Query(100, ge=1, le=1000),
                    db: Session = Depends(get_db)):
    rows = list_safety_events(db, limit)
    return [
        EventOut(
            id=r.id,
            timestamp=_iso(r.timestamp) + "Z",
            zone_id=r.zone_id,
            zone_name=r.zone_name,
            state=r.state,
            clearance=r.clearance,
            ratio=r.ratio,
        )
        for r in rows
    ]


@router.post("/safety/events")
def api_add_event(body: EventIn, db: Session = Depends(get_db)):
    """记录一条报警事件。只在状态变严重/恢复时调用，避免每帧刷库。"""
    if body.state not in ("danger", "hit", "clear"):
        raise AppError("state 必须是 danger / hit / clear", 400, "BAD_SAFETY_STATE")
    row = insert_safety_event(
        db, body.zone_id, body.zone_name, body.state, body.clearance, body.ratio
    )
    return {"ok": True, "id": row.id}


@router.delete("/safety/events")
def api_clear_events(db: Session = Depends(get_db)):
    n = clear_safety_events(db)
    return {"ok": True, "deleted": n}
