# -*- coding: utf-8 -*-
"""REST 接口: 录制(动作序列)的增删改查 + 导出/导入。"""
from __future__ import annotations

import json
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.db.crud import (
    create_recording,
    delete_recording,
    get_recording,
    list_recordings,
    update_recording,
)

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

EXPORT_FORMAT = "efort-recording/v1"


def _norm_source(s: Optional[str]) -> str:
    return s if s in ("real", "sim") else "sim"


# ---------- Schemas ----------
class Frame(BaseModel):
    t: float = 0.0                                  # 相对录制起点 ms
    j1: float = 0.0
    j2: float = 0.0
    j3: float = 0.0
    j4: float = 0.0
    j5: float = 0.0
    j6: float = 0.0


class RecordingIn(BaseModel):
    name: str = Field(default="未命名录制", max_length=128)
    description: str = Field(default="", max_length=512)
    source: str = Field(default="sim")
    duration_ms: int = Field(default=0, ge=0)
    frames: List[Frame] = Field(default_factory=list)


class RecordingUpdate(BaseModel):
    """部分更新: 只改传入的字段。"""
    name: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    duration_ms: Optional[int] = Field(default=None, ge=0)
    frames: Optional[List[Frame]] = None


class ImportIn(BaseModel):
    """导入文件体 (兼容缺 format 字段的旧文件)。"""
    format: Optional[str] = None
    name: str = Field(default="导入录制", max_length=128)
    description: str = Field(default="", max_length=512)
    source: str = Field(default="sim")
    duration_ms: Optional[int] = None
    frames: List[Frame] = Field(default_factory=list)


def _summary(row) -> dict:
    n_frames = 0
    try:
        n_frames = len(json.loads(row.frames or "[]"))
    except Exception:
        pass
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "source": getattr(row, "source", None) or "sim",
        "duration_ms": row.duration_ms,
        "frames_count": n_frames,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def _detail(row) -> dict:
    d = _summary(row)
    try:
        d["frames"] = json.loads(row.frames or "[]")
    except Exception:
        d["frames"] = []
    return d


# ---------- 导入 (放在 /{rid} 之前, 避免路径歧义) ----------
@router.post("/import")
def api_import_recording(body: ImportIn, db: Session = Depends(get_db)):
    frames_json = json.dumps([f.model_dump() for f in body.frames], ensure_ascii=False)
    dur = body.duration_ms
    if dur is None or dur <= 0:
        dur = int(body.frames[-1].t) if body.frames else 0
    row = create_recording(db, body.name, frames_json, dur,
                           body.description, _norm_source(body.source))
    return _detail(row)


# ---------- 列表 / 创建 ----------
@router.get("")
def api_list_recordings(db: Session = Depends(get_db)):
    return [_summary(r) for r in list_recordings(db)]


@router.post("")
def api_create_recording(body: RecordingIn, db: Session = Depends(get_db)):
    frames_json = json.dumps([f.model_dump() for f in body.frames], ensure_ascii=False)
    row = create_recording(db, body.name, frames_json, body.duration_ms,
                           body.description, _norm_source(body.source))
    return _detail(row)


# ---------- 导出 ----------
@router.get("/{rid}/export")
def api_export_recording(rid: int, db: Session = Depends(get_db)):
    row = get_recording(db, rid)
    if not row:
        raise HTTPException(404, "录制不存在")
    payload = {
        "format": EXPORT_FORMAT,
        "name": row.name,
        "description": row.description,
        "source": getattr(row, "source", None) or "sim",
        "duration_ms": row.duration_ms,
        "frames": json.loads(row.frames or "[]"),
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    fname = f"{(row.name or 'recording').strip()}.json"
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
        },
    )


@router.get("/{rid}")
def api_get_recording(rid: int, db: Session = Depends(get_db)):
    row = get_recording(db, rid)
    if not row:
        raise HTTPException(404, "录制不存在")
    return _detail(row)


@router.put("/{rid}")
def api_update_recording(rid: int, body: RecordingUpdate, db: Session = Depends(get_db)):
    frames_json = None
    if body.frames is not None:
        frames_json = json.dumps([f.model_dump() for f in body.frames], ensure_ascii=False)
    row = update_recording(db, rid, name=body.name, description=body.description,
                           frames_json=frames_json, duration_ms=body.duration_ms)
    if not row:
        raise HTTPException(404, "录制不存在")
    return _detail(row)


@router.delete("/{rid}")
def api_delete_recording(rid: int, db: Session = Depends(get_db)):
    if not delete_recording(db, rid):
        raise HTTPException(404, "录制不存在")
    return {"ok": True, "id": rid}
