# -*- coding: utf-8 -*-
"""ORM 表定义 (Base + 三张表)。引擎/会话见 database.py。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PoseHistory(Base):
    __tablename__ = "pose_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    j1: Mapped[float] = mapped_column(Float, default=0.0)
    j2: Mapped[float] = mapped_column(Float, default=0.0)
    j3: Mapped[float] = mapped_column(Float, default=0.0)
    j4: Mapped[float] = mapped_column(Float, default=0.0)
    j5: Mapped[float] = mapped_column(Float, default=0.0)
    j6: Mapped[float] = mapped_column(Float, default=0.0)
    tcp_x: Mapped[float] = mapped_column(Float, default=0.0)  # 末端 TCP (mm)
    tcp_y: Mapped[float] = mapped_column(Float, default=0.0)
    tcp_z: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(16), default="real")  # real | replay


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(8), default="INFO")  # INFO|WARN|ERROR
    message: Mapped[str] = mapped_column(String(512), default="")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    label: Mapped[str] = mapped_column(String(64), default="")


class SafetyEvent(Base):
    """安全围栏报警事件（时间线用）。

    只在状态"变严重"时写入（进入 danger / hit），以及从 danger/hit 恢复时写一条
    state=clear，避免每帧刷库。
    """

    __tablename__ = "safety_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    zone_id: Mapped[str] = mapped_column(String(32), default="")
    zone_name: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(16), default="")  # danger | hit | clear
    clearance: Mapped[float] = mapped_column(Float, default=0.0)  # 余量(m)
    ratio: Mapped[float] = mapped_column(Float, default=0.0)  # 余量百分比 0~1


class Recording(Base):
    """录制的动作序列: 帧存 JSON 文本 [{t, j1..j6}], t 为相对录制起点毫秒。

    source: real = 录制真机动作; sim = 模拟视图键盘摆出的动作。
    """

    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), default="未命名录制")
    description: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(16), default="sim")  # real | sim
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    frames: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
