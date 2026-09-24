# -*- coding: utf-8 -*-
"""REST 接口: 预设点位 (Point) 的增删改查 + 批量导入/导出。"""
from __future__ import annotations

import json
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import require_control, token_role
from app.core.deps import get_db
from app.db.crud import (
    create_point,
    delete_point,
    get_point,
    list_points,
    update_point,
)
from app.services.events import emit as emit_event

router = APIRouter(prefix="/api/points", tags=["points"])

EXPORT_FORMAT = "efort-point/v1"


def _actor(tok: str) -> str:
    return token_role(tok) or "unknown"


def _norm_kind(s: Optional[str]) -> str:
    return s if s in ("joint", "cartesian") else "joint"


def _norm_joints(v) -> List[float]:
    """接受 [6] 或 {j1..j6}, 统一成 [j1..j6] 且限位裁剪交由运动学/执行层。"""
    if isinstance(v, dict):
        return [float(v.get(f"j{i}", 0.0)) for i in range(1, 7)]
    arr = [float(x) for x in (v or [0, 0, 0, 0, 0, 0])]
    if len(arr) != 6:
        raise ValueError("joints 必须为 6 个数值")
    return arr


class PointIn(BaseModel):
    name: str = Field(default="未命名点位", max_length=128)
    group: str = Field(default="默认", max_length=64)
    kind: str = Field(default="joint")
    joints: List[float] = Field(default_factory=lambda: [0, 0, 0, 0, 0, 0])
    tcp: Optional[dict] = None          # 直角坐标模式目标 {x,y,z,rx,ry,rz}
    note: str = Field(default="", max_length=512)


class PointUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    group: Optional[str] = Field(default=None, max_length=64)
    kind: Optional[str] = None
    joints: Optional[List[float]] = None
    tcp: Optional[dict] = None
    note: Optional[str] = Field(default=None, max_length=512)


class ImportIn(BaseModel):
    format: Optional[str] = None
    name: str = Field(default="导入点位", max_length=128)
    group: str = Field(default="默认", max_length=64)
    kind: str = Field(default="joint")
    joints: List[float] = Field(default_factory=lambda: [0, 0, 0, 0, 0, 0])
    tcp: Optional[dict] = None
    note: str = Field(default="", max_length=512)


def _summary(row) -> dict:
    try:
        joints = json.loads(row.joints or "[0,0,0,0,0,0]")
    except Exception:
        joints = [0, 0, 0, 0, 0, 0]
    return {
        "id": row.id,
        "name": row.name,
        "group": row.group,
        "kind": row.kind,
        "joints": [round(float(x), 3) for x in joints],
        "tcp": _safe_tcp(row.tcp),
        "note": row.note,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def _detail(row) -> dict:
    d = _summary(row)
    return d


def _safe_tcp(raw: str):
    try:
        v = json.loads(raw or "null")
        return v
    except Exception:
        return None


def _joints_json(body) -> str:
    return json.dumps(_norm_joints(body.joints), ensure_ascii=False)


def _tcp_json(body) -> str:
    if getattr(body, "tcp", None) is None:
        return "null"
    return json.dumps(body.tcp, ensure_ascii=False)


# ---------- 导入 (放 /{pid} 之前) ----------
# ★ 写操作统一要求控制令牌（阶段 4）：点位决定机器人往哪走，不能让人随手改。
@router.post("/import")
def api_import_point(body: ImportIn, db: Session = Depends(get_db),
                     tok: str = Depends(require_control)):
    row = create_point(db, body.name, body.group, _norm_kind(body.kind),
                       _joints_json(body), _tcp_json(body), body.note)
    emit_event("config", "info", "point.import", f"已导入点位「{row.name}」",
               {"id": row.id, "name": row.name, "group": row.group},
               actor=_actor(tok))
    return _detail(row)


# ---------- 列表 / 创建 ----------
@router.get("")
def api_list_points(group: Optional[str] = None, db: Session = Depends(get_db)):
    return [_summary(r) for r in list_points(db, group)]


@router.post("")
def api_create_point(body: PointIn, db: Session = Depends(get_db),
                     tok: str = Depends(require_control)):
    row = create_point(db, body.name, body.group, _norm_kind(body.kind),
                       _joints_json(body), _tcp_json(body), body.note)
    emit_event("config", "info", "point.create", f"已新建点位「{row.name}」",
               {"id": row.id, "name": row.name, "group": row.group},
               actor=_actor(tok))
    return _detail(row)


# ---------- 导出 ----------
@router.get("/{pid}/export")
def api_export_point(pid: int, db: Session = Depends(get_db)):
    row = get_point(db, pid)
    if not row:
        raise HTTPException(404, "点位不存在")
    payload = {
        "format": EXPORT_FORMAT,
        "name": row.name,
        "group": row.group,
        "kind": row.kind,
        "joints": json.loads(row.joints or "[0,0,0,0,0,0]"),
        "tcp": _safe_tcp(row.tcp),
        "note": row.note,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    fname = f"{(row.name or 'point').strip()}.json"
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/{pid}")
def api_get_point(pid: int, db: Session = Depends(get_db)):
    row = get_point(db, pid)
    if not row:
        raise HTTPException(404, "点位不存在")
    return _detail(row)


@router.put("/{pid}")
def api_update_point(pid: int, body: PointUpdate, db: Session = Depends(get_db),
                     tok: str = Depends(require_control)):
    js = None
    if body.joints is not None:
        js = json.dumps(_norm_joints(body.joints), ensure_ascii=False)
    tc = None if body.tcp is None else json.dumps(body.tcp, ensure_ascii=False)
    row = update_point(db, pid, name=body.name, group=body.group, kind=body.kind,
                       joints_json=js, tcp_json=tc, note=body.note)
    if not row:
        raise HTTPException(404, "点位不存在")
    emit_event("config", "info", "point.update", f"已更新点位「{row.name}」",
               {"id": row.id, "name": row.name}, actor=_actor(tok))
    return _detail(row)


@router.delete("/{pid}")
def api_delete_point(pid: int, db: Session = Depends(get_db),
                     tok: str = Depends(require_control)):
    row = get_point(db, pid)
    if not delete_point(db, pid):
        raise HTTPException(404, "点位不存在")
    emit_event("config", "warn", "point.delete",
               f"已删除点位「{(row.name if row else pid)}」",
               {"id": pid, "name": (row.name if row else "")}, actor=_actor(tok))
    return {"ok": True, "id": pid}
