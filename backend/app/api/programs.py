# -*- coding: utf-8 -*-
"""REST 接口: 执行程序 (Program, 有序点位序列) 的增删改查。"""
from __future__ import annotations

import json
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.safety_const import SPEED_MIN, SPEED_MAX
from app.api.auth import require_control, token_role
from app.core.deps import get_db
from app.db.crud import (
    create_program,
    delete_program,
    get_program,
    list_programs,
    update_program,
)
from app.services.events import emit as emit_event

router = APIRouter(prefix="/api/programs", tags=["programs"])

EXPORT_FORMAT = "efort-program/v1"


def _actor(tok: str) -> str:
    return token_role(tok) or "unknown"


class ProgramItem(BaseModel):
    point_id: int
    # ★ 全维度审查 C-16：原 default=100 / ge=1 与控制层（default=5 / ge=5）口径相反，
    #   构成一条绕过 5% 下限的通道。统一到安全常量。
    speed_pct: int = Field(default=SPEED_MIN, ge=SPEED_MIN, le=SPEED_MAX)
    dwell_ms: int = Field(default=0, ge=0, le=60000)


class ProgramIn(BaseModel):
    name: str = Field(default="未命名程序", max_length=128)
    note: str = Field(default="", max_length=512)
    items: List[ProgramItem] = Field(default_factory=list)


class ProgramUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    note: Optional[str] = Field(default=None, max_length=512)
    items: Optional[List[ProgramItem]] = None


def _items_json(items: List[ProgramItem]) -> str:
    return json.dumps([it.model_dump() for it in items], ensure_ascii=False)


def _summary(row) -> dict:
    try:
        items = json.loads(row.items or "[]")
    except Exception:
        items = []
    return {
        "id": row.id,
        "name": row.name,
        "note": row.note,
        "items": items,
        "items_count": len(items),
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


# ---------- 列表 / 创建 ----------
@router.get("")
def api_list_programs(db: Session = Depends(get_db)):
    return [_summary(r) for r in list_programs(db)]


@router.post("")
def api_create_program(body: ProgramIn, db: Session = Depends(get_db),
                       tok: str = Depends(require_control)):
    row = create_program(db, body.name, _items_json(body.items), body.note)
    emit_event("config", "info", "program.create", f"已新建程序「{row.name}」",
               {"id": row.id, "name": row.name, "steps": len(body.items)},
               actor=_actor(tok))
    return _summary(row)


@router.get("/{prid}")
def api_get_program(prid: int, db: Session = Depends(get_db)):
    row = get_program(db, prid)
    if not row:
        raise HTTPException(404, "程序不存在")
    return _summary(row)


@router.put("/{prid}")
def api_update_program(prid: int, body: ProgramUpdate, db: Session = Depends(get_db),
                       tok: str = Depends(require_control)):
    ij = None if body.items is None else _items_json(body.items)
    row = update_program(db, prid, name=body.name, note=body.note, items_json=ij)
    if not row:
        raise HTTPException(404, "程序不存在")
    emit_event("config", "info", "program.update", f"已更新程序「{row.name}」",
               {"id": prid, "name": row.name}, actor=_actor(tok))
    return _summary(row)


@router.delete("/{prid}")
def api_delete_program(prid: int, db: Session = Depends(get_db),
                       tok: str = Depends(require_control)):
    row = get_program(db, prid)
    if not delete_program(db, prid):
        raise HTTPException(404, "程序不存在")
    emit_event("config", "warn", "program.delete",
               f"已删除程序「{(row.name if row else prid)}」",
               {"id": prid, "name": (row.name if row else "")}, actor=_actor(tok))
    return {"ok": True, "id": prid}


@router.get("/{prid}/export")
def api_export_program(prid: int, db: Session = Depends(get_db)):
    row = get_program(db, prid)
    if not row:
        raise HTTPException(404, "程序不存在")
    payload = {
        "format": EXPORT_FORMAT,
        "name": row.name,
        "note": row.note,
        "items": json.loads(row.items or "[]"),
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    fname = f"{(row.name or 'program').strip()}.json"
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )
