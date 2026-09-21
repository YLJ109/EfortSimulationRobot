# -*- coding: utf-8 -*-
"""数据库读写辅助。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import Event, PoseHistory, SafetyEvent, Session


def insert_pose(session, j1, j2, j3, j4, j5, j6, tcp, source="real") -> PoseHistory:
    row = PoseHistory(
        timestamp=datetime.now(timezone.utc),
        j1=j1, j2=j2, j3=j3, j4=j4, j5=j5, j6=j6,
        tcp_x=tcp[0], tcp_y=tcp[1], tcp_z=tcp[2],
        source=source,
    )
    session.add(row)
    session.commit()
    return row


def insert_event(session, level: str, message: str) -> Event:
    row = Event(timestamp=datetime.now(timezone.utc), level=level, message=message)
    session.add(row)
    session.commit()
    return row


def get_recent_poses(session, limit: int = 500):
    stmt = (
        select(PoseHistory)
        .order_by(PoseHistory.id.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return list(reversed(rows))


def get_latest_pose(session) -> PoseHistory | None:
    stmt = select(PoseHistory).order_by(PoseHistory.id.desc()).limit(1)
    return session.execute(stmt).scalars().first()


def start_session(session, label: str = "") -> Session:
    row = Session(started_at=datetime.now(timezone.utc), label=label)
    session.add(row)
    session.commit()
    return row


def end_session(session, sid: int) -> None:
    row = session.get(Session, sid)
    if row:
        row.ended_at = datetime.now(timezone.utc)
        session.commit()


def prune_old_poses(session, retention_days: int) -> int:
    """删除超过保留天数的历史行, 返回删除条数。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    stmt = select(PoseHistory).where(PoseHistory.timestamp < cutoff)
    rows = session.execute(stmt).scalars().all()
    n = len(rows)
    for r in rows:
        session.delete(r)
    session.commit()
    return n


# ---------------- 录制 CRUD ----------------
from app.db.models import Recording  # noqa: E402


def list_recordings(session):
    """录制列表(按 updated_at 倒序)。"""
    stmt = select(Recording).order_by(Recording.updated_at.desc())
    return list(session.execute(stmt).scalars().all())


def get_recording(session, rid: int) -> Recording | None:
    return session.get(Recording, rid)


def create_recording(session, name: str, frames_json: str,
                     duration_ms: int, description: str = "",
                     source: str = "sim") -> Recording:
    now = datetime.now(timezone.utc)
    row = Recording(name=name, description=description, source=source,
                    duration_ms=duration_ms,
                    frames=frames_json, created_at=now, updated_at=now)
    session.add(row)
    session.commit()
    return row


def update_recording(session, rid: int, name=None, description=None,
                     frames_json=None, duration_ms=None) -> Recording | None:
    row = session.get(Recording, rid)
    if not row:
        return None
    if name is not None:
        row.name = name
    if description is not None:
        row.description = description
    if frames_json is not None:
        row.frames = frames_json
    if duration_ms is not None:
        row.duration_ms = duration_ms
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return row


def delete_recording(session, rid: int) -> bool:
    row = session.get(Recording, rid)
    if not row:
        return False
    session.delete(row)
    session.commit()
    return True


# =====================================================================
# 安全围栏报警事件 (时间线)
# =====================================================================
def insert_safety_event(session, zone_id: str, zone_name: str, state: str,
                        clearance: float, ratio: float) -> SafetyEvent:
    row = SafetyEvent(
        timestamp=datetime.now(timezone.utc),
        zone_id=str(zone_id or ""),
        zone_name=str(zone_name or ""),
        state=str(state or ""),
        clearance=float(clearance or 0.0),
        ratio=float(ratio or 0.0),
    )
    session.add(row)
    session.commit()
    return row


def list_safety_events(session, limit: int = 100):
    stmt = select(SafetyEvent).order_by(SafetyEvent.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def clear_safety_events(session) -> int:
    n = session.query(SafetyEvent).delete()
    session.commit()
    return int(n)
