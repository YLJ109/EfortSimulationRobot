# -*- coding: utf-8 -*-
"""
统一事件时间线 / 审计日志 API（阶段 3 + 阶段 4）。

GET    /api/events          事件列表（类别/级别/操作者/动作/关键字/时间窗 过滤 + 分页）
GET    /api/events/stats    概览统计（按类别、按级别）
GET    /api/events/export   导出 JSON / CSV（Excel -friendly UTF-8 BOM）
DELETE /api/events          清理历史（★ 管理员权限；?days=N 只清 N 天前，缺省全清）

数据来自 services/events.py 的 emit()，覆盖：登录失败/成功、登出、下发/急停、
点位与程序增删改、安全围栏报警、机器人掉线/恢复、服务启停、备份导入导出。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.auth import require_admin, token_role
from app.core.deps import get_db
from app.db import crud
from app.services.events import emit as emit_event
from app.services.events import since_dt

router = APIRouter(prefix="/api/events", tags=["events"])

_EXPORT_FORMAT = "efort-events/v1"


def _iso(dt, naive=False) -> str:
    try:
        if dt is None:
            return ""
        if naive:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.isoformat() + "Z" if dt.tzinfo is None else dt.isoformat()
    except Exception:
        return ""


def _row(r) -> dict:
    return {
        "id": r.id,
        "timestamp": _iso(r.timestamp),
        "category": r.category,
        "level": r.level,
        "action": r.action,
        "actor": r.actor,
        "ip": r.ip,
        "message": r.message,
        "detail": r.detail,
    }


@router.get("")
def api_list_events(
    category: Optional[str] = Query(None, description="system|connection|auth|control|config|safety"),
    level: Optional[str] = Query(None, description="最低级别：debug|info|warn|error|critical"),
    actor: Optional[str] = Query(None, description="system|admin|operator|anonymous"),
    action: Optional[str] = Query(None, description="精确动作名，如 control.move"),
    q: Optional[str] = Query(None, description="消息/动作关键字"),
    hours: Optional[float] = Query(None, ge=0, description="最近 N 小时"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    since = since_dt(hours)
    rows = crud.list_system_events(
        db, category=category, level=level, actor=actor, action=action,
        q=q, since=since, limit=limit, offset=offset,
    )
    # total 为"全部匹配条数"（不受分页影响），供前端判断是否还有更多
    total = crud.count_system_events(db, since)
    return {"items": [_row(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


@router.get("/stats")
def api_stats(
    hours: Optional[float] = Query(None, ge=0, description="最近 N 小时"),
    db: Session = Depends(get_db),
):
    since = since_dt(hours)
    return crud.system_event_stats(db, since)


@router.get("/export")
def api_export_events(
    format: str = Query("json", description="json | csv"),
    category: Optional[str] = None,
    level: Optional[str] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    q: Optional[str] = None,
    hours: Optional[float] = None,
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    since = since_dt(hours)
    rows = crud.list_system_events(
        db, category=category, level=level, actor=actor, action=action,
        q=q, since=since, limit=limit, offset=0,
    )
    items = [_row(r) for r in rows]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "timestamp", "category", "level", "action",
                    "actor", "ip", "message", "detail"])
        for it in items:
            w.writerow([it["id"], it["timestamp"], it["category"], it["level"],
                        it["action"], it["actor"], it["ip"], it["message"],
                        it["detail"]])
        # utf-8-sig：Excel 直接打开不乱码
        payload = buf.getvalue().encode("utf-8-sig")
        return StreamingResponse(
            iter([payload]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f"attachment; filename=efort_events_{stamp}.csv"},
        )

    import json as _json
    body = {
        "format": _EXPORT_FORMAT,
        "exported_at": datetime.now(timezone.utc).isoformat() + "Z",
        "filters": {"category": category, "level": level, "actor": actor,
                    "action": action, "q": q, "hours": hours},
        "count": len(items),
        "items": items,
    }
    return StreamingResponse(
        iter([_json.dumps(body, ensure_ascii=False, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition":
                 f"attachment; filename=efort_events_{stamp}.json"},
    )


@router.delete("")
def api_clear_events(
    days: Optional[float] = Query(None, ge=0,
                                  description="只清理 N 天前的记录；不传或 0 表示全部清空"),
    tok: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """★ 管理员才能清理审计日志（防止操作员抹掉操作痕迹）。"""
    n = crud.prune_system_events(db, days or 0)
    emit_event("config", "warn" if not days else "info", "events.clear",
               f"审计日志清理：删除 {n} 条（days={days or 0}）",
               {"deleted": n, "days": days or 0},
               actor=token_role(tok) or "unknown")
    return {"ok": True, "deleted": n}
