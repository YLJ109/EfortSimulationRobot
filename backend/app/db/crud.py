# -*- coding: utf-8 -*-
"""数据库读写辅助。"""
from __future__ import annotations

import json
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
# 预设点位 (Point) CRUD
# =====================================================================
from app.db.models import Point, Program  # noqa: E402


def list_points(session, group: str | None = None):
    stmt = select(Point)
    if group:
        stmt = stmt.where(Point.group == group)
    stmt = stmt.order_by(Point.updated_at.desc())
    return list(session.execute(stmt).scalars().all())


def get_point(session, pid: int) -> Point | None:
    return session.get(Point, pid)


def create_point(session, name: str, group: str = "默认", kind: str = "joint",
                 joints_json: str = "[0,0,0,0,0,0]", tcp_json: str = "null",
                 note: str = "") -> Point:
    now = datetime.now(timezone.utc)
    row = Point(name=name, group=group, kind=kind, joints=joints_json,
                tcp=tcp_json, note=note, created_at=now, updated_at=now)
    session.add(row)
    session.commit()
    return row


def update_point(session, pid: int, name=None, group=None, kind=None,
                 joints_json=None, tcp_json=None, note=None) -> Point | None:
    row = session.get(Point, pid)
    if not row:
        return None
    if name is not None:
        row.name = name
    if group is not None:
        row.group = group
    if kind is not None:
        row.kind = kind
    if joints_json is not None:
        row.joints = joints_json
    if tcp_json is not None:
        row.tcp = tcp_json
    if note is not None:
        row.note = note
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return row


def delete_point(session, pid: int) -> bool:
    row = session.get(Point, pid)
    if not row:
        return False
    session.delete(row)
    session.commit()
    return True


# =====================================================================
# 执行程序 (Program) CRUD
# =====================================================================
def list_programs(session):
    stmt = select(Program).order_by(Program.updated_at.desc())
    return list(session.execute(stmt).scalars().all())


def get_program(session, prid: int) -> Program | None:
    return session.get(Program, prid)


def create_program(session, name: str, items_json: str = "[]", note: str = "") -> Program:
    now = datetime.now(timezone.utc)
    row = Program(name=name, note=note, items=items_json, created_at=now, updated_at=now)
    session.add(row)
    session.commit()
    return row


def update_program(session, prid: int, name=None, note=None, items_json=None) -> Program | None:
    row = session.get(Program, prid)
    if not row:
        return None
    if name is not None:
        row.name = name
    if note is not None:
        row.note = note
    if items_json is not None:
        row.items = items_json
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return row


def delete_program(session, prid: int) -> bool:
    row = session.get(Program, prid)
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


# =====================================================================
# 统一事件总线 / 审计日志 (system_events)
# =====================================================================
from app.db.models import SystemEvent, SafetyConfigVersion  # noqa: E402


def insert_system_event(session, category: str, level: str, action: str,
                        message: str = "", detail: str = "",
                        actor: str = "system", ip: str = "") -> SystemEvent:
    row = SystemEvent(
        timestamp=datetime.now(timezone.utc),
        category=str(category or "system")[:16],
        level=str(level or "info")[:8],
        action=str(action or "")[:64],
        actor=str(actor or "system")[:16],
        ip=str(ip or "")[:64],
        message=str(message or "")[:512],
        detail=detail or "",
    )
    session.add(row)
    session.commit()
    return row


def list_system_events(session, category: str | None = None, level: str | None = None,
                       actor: str | None = None, action: str | None = None,
                       q: str | None = None, since=None, limit: int = 200,
                       offset: int = 0):
    """事件列表（默认最新在前）。所有过滤条件都是可选的与关系。"""
    stmt = select(SystemEvent)
    if category:
        stmt = stmt.where(SystemEvent.category == category)
    if level:
        # level 按"最低级别"过滤（如 level=warn 表示 warn 及以上）
        lvls = _at_or_above(level)
        stmt = stmt.where(SystemEvent.level.in_(lvls))
    if actor:
        stmt = stmt.where(SystemEvent.actor == actor)
    if action:
        stmt = stmt.where(SystemEvent.action == action)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (SystemEvent.message.like(like)) | (SystemEvent.action.like(like))
        )
    if since is not None:
        stmt = stmt.where(SystemEvent.timestamp >= since)
    stmt = stmt.order_by(SystemEvent.id.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars())


_LEVELS = ("debug", "info", "warn", "error", "critical")


def _at_or_above(level: str):
    """返回 >= level 的所有级别（用于"最低级别"过滤）。"""
    lv = str(level or "").lower()
    if lv not in _LEVELS:
        return [lv] if lv else []
    return list(_LEVELS[_LEVELS.index(lv):])


def count_system_events(session, since=None) -> int:
    stmt = select(SystemEvent)
    if since is not None:
        stmt = stmt.where(SystemEvent.timestamp >= since)
    return len(list(session.execute(stmt).scalars()))


def system_event_stats(session, since=None) -> dict:
    """按类别/级别统计条数（时间线页头部概览）。"""
    rows = list(session.execute(select(SystemEvent)).scalars())
    if since is not None:
        rows = [r for r in rows if r.timestamp >= since]
    by_cat: dict = {}
    by_lv: dict = {}
    for r in rows:
        by_cat[r.category] = by_cat.get(r.category, 0) + 1
        by_lv[r.level] = by_lv.get(r.level, 0) + 1
    return {"total": len(rows), "by_category": by_cat, "by_level": by_lv}


def prune_system_events(session, days: int) -> int:
    """删除 N 天前的事件，返回删除条数。days<=0 表示清空。"""
    if days and days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = list(session.execute(
            select(SystemEvent).where(SystemEvent.timestamp < cutoff)
        ).scalars())
    else:
        rows = list(session.execute(select(SystemEvent)).scalars())
    n = len(rows)
    for r in rows:
        session.delete(r)
    session.commit()
    return n


# =====================================================================
# 安全围栏配置版本 (safety_config_versions)
# =====================================================================
def insert_safety_version(session, config_json: str, actor: str = "system",
                           source: str = "save", note: str = "") -> SafetyConfigVersion:
    row = SafetyConfigVersion(
        created_at=datetime.now(timezone.utc),
        actor=str(actor or "system")[:16],
        source=str(source or "save")[:16],
        note=str(note or "")[:255],
        config=config_json,
    )
    session.add(row)
    session.commit()
    return row


def list_safety_versions(session, limit: int = 50):
    stmt = (select(SafetyConfigVersion)
            .order_by(SafetyConfigVersion.id.desc())
            .limit(limit))
    return list(session.execute(stmt).scalars())


def get_safety_version(session, vid: int) -> SafetyConfigVersion | None:
    return session.get(SafetyConfigVersion, vid)


def prune_safety_versions(session, keep: int = 30) -> int:
    """只保留最近 keep 条历史版本，返回清理条数。"""
    rows = list(session.execute(
        select(SafetyConfigVersion).order_by(SafetyConfigVersion.id.desc())
    ).scalars())
    extra = rows[keep:]
    for r in extra:
        session.delete(r)
    if extra:
        session.commit()
    return len(extra)


# ============================ 视觉分拣记录 ============================
from app.db.models import VisionRecord  # noqa: E402


def insert_vision_record(session, data: dict) -> VisionRecord | None:
    """幂等写入一条视觉记录。

    ★ 用 seq 做唯一键: 后端重启后会从 since=0 重放相机服务的最近事件,
      没有幂等保证就会出现重复记录。已存在直接返回 None。
    """
    seq = int(data.get("seq") or 0)
    if seq > 0:
        exist = session.execute(
            select(VisionRecord).where(VisionRecord.seq == seq)
        ).scalars().first()
        if exist is not None:
            return None
    lab = data.get("lab")
    lab_s = ""
    if isinstance(lab, (list, tuple)) and len(lab) >= 3:
        lab_s = "%.2f,%.2f,%.2f" % (float(lab[0]), float(lab[1]), float(lab[2]))
    center = data.get("center")
    center_s = ""
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        center_s = "%.1f,%.1f" % (float(center[0]), float(center[1]))
    images = data.get("images") or {}
    now = datetime.now(timezone.utc)
    row = VisionRecord(
        seq=seq,
        timestamp=now,
        color=str(data.get("color") or "")[:16],
        hex=str(data.get("hex") or "")[:9],
        conf=float(data.get("conf") or 0.0),
        de=float(data.get("de") or 0.0),
        lab=lab_s,
        ratio=float(data.get("ratio") or 0.0),
        alt=str(data.get("alt") or "")[:16],
        center=center_s,
        area=int(data.get("area") or 0),
        reason=str(data.get("reason") or "")[:64],
        images=json.dumps(images, ensure_ascii=False),
        program_id=data.get("program_id"),
        program_name=str(data.get("program_name") or "")[:64],
        outcome=str(data.get("outcome") or "recorded")[:16],
        note=str(data.get("note") or "")[:255],
        source=str(data.get("source") or "camera")[:16],
        created_at=now,
    )
    session.add(row)
    session.commit()
    return row


def list_vision_records(session, color: str | None = None, since=None,
                        limit: int = 100, offset: int = 0):
    stmt = select(VisionRecord)
    if color:
        stmt = stmt.where(VisionRecord.color == color)
    if since is not None:
        stmt = stmt.where(VisionRecord.timestamp >= since)
    stmt = stmt.order_by(VisionRecord.id.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars())


def count_vision_records(session, color: str | None = None, since=None) -> int:
    from sqlalchemy import func
    stmt = select(func.count(VisionRecord.id))
    if color:
        stmt = stmt.where(VisionRecord.color == color)
    if since is not None:
        stmt = stmt.where(VisionRecord.timestamp >= since)
    return int(session.execute(stmt).scalar() or 0)


def vision_stats(session, since=None) -> dict:
    """按颜色统计 + 总数（现场最常问"今天红的有多少"）。"""
    from sqlalchemy import func
    stmt = select(VisionRecord.color, func.count(VisionRecord.id))
    if since is not None:
        stmt = stmt.where(VisionRecord.timestamp >= since)
    stmt = stmt.group_by(VisionRecord.color)
    by_color = {str(c or "?"): int(n) for c, n in session.execute(stmt).all()}
    return {"total": sum(by_color.values()), "by_color": by_color,
            "colors": len(by_color)}


def get_vision_record(session, rid: int) -> VisionRecord | None:
    return session.get(VisionRecord, rid)


def update_vision_record(session, rid: int, **fields) -> VisionRecord | None:
    row = session.get(VisionRecord, rid)
    if row is None:
        return None
    for k, v in fields.items():
        if v is not None and hasattr(row, k):
            setattr(row, k, v)
    session.commit()
    return row


def prune_vision_records(session, days: int) -> int:
    """删除 N 天前的视觉记录，返回条数。"""
    if days <= 0:
        n = session.query(VisionRecord).count()
        session.query(VisionRecord).delete()
        session.commit()
        return int(n)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = list(session.execute(
        select(VisionRecord).where(VisionRecord.timestamp < cutoff)
    ).scalars())
    for r in rows:
        session.delete(r)
    if rows:
        session.commit()
    return len(rows)
