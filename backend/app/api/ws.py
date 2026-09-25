# -*- coding: utf-8 -*-
"""WebSocket 实时推送：姿态流 `/ws/pose` + 事件流 `/ws/events`。

★ 审计修复 P1-B6：浏览器 WebSocket **不受 CORS 保护**，只靠 HTTP 头挡不住
  任意网页连上来订阅数据。这里补齐三件事：

  1. **Origin 校验** —— 非本机/非白名单来源一律拒绝（非浏览器客户端不带
     Origin 头，放行，不影响 curl/wscat 排障）；
  2. **连接数上限** —— 每条流 64 个，超出回 1013（试着重连），防资源耗尽；
  3. **事件流与姿态流分流** —— `/ws/events` 推的是审计帧（actor/ip/登录结果），
     必须先出示控制令牌才能订阅；`/ws/pose` 保持公开（姿态是本系统的基础
     监控数据，各页面底栏/3D 都要，且不含审计信息）。

  令牌用**首条消息**而不是 `?token=` 查询参数传：查询串会进 uvicorn 访问日志，
  等于把令牌写进日志（与 P1-B9"口令不进日志"是同一条纪律）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from urllib.parse import urlparse

from app.api.auth import valid_token
from app.core.config import get_config
from app.services.collector import collector
from app.services.hub import hub

router = APIRouter()

# 每条流的连接数上限（审计 P1-B6：无上限时可被脚本开一万个连接耗尽内存/FD）
MAX_CLIENTS_PER_STREAM = 64


def _allowed_origins(host_header: str) -> Set[str]:
    """允许的 WS 来源：开发用 vite 白名单 + 本服务自己的地址（同源托管）。"""
    cfg = get_config()
    origins = {
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    }
    try:
        port = int(cfg.get("server", "port", default=8000) or 8000)
    except Exception:
        port = 8000
    # 同源托管：前端 dist 由本服务提供，Origin 形如 http://<Host 头>
    host = (host_header or "").split(",")[0].strip()
    if host:
        origins.add(f"http://{host}")
        origins.add(f"https://{host}")
    for h in (f"127.0.0.1:{port}", f"localhost:{port}"):
        origins.add(f"http://{h}")
        origins.add(f"https://{h}")
    return origins


def _origin_ok(ws: WebSocket) -> bool:
    """Origin 缺省（非浏览器客户端：curl/wscat/脚本）放行；有则必须在白名单内。

    ★ 浏览器 WebSocket 不受 CORS 保护 —— 任意网页都能连上来，
      服务端不校验 Origin 等于把姿态流敞开给全网的浏览器标签页。
    """
    origin = (ws.headers.get("origin") or "").strip()
    if not origin:
        return True
    if origin in _allowed_origins(ws.headers.get("host", "")):
        return True
    # scheme 不一致（https 反代）时只比主机名部分
    try:
        p = urlparse(origin)
        host_hdr = (ws.headers.get("host") or "").split(",")[0].strip()
        if p.netloc and p.netloc == host_hdr:
            return True
    except Exception:
        pass
    return False


async def _deny(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        pass


async def _wait_token(ws: WebSocket, timeout: float = 5.0) -> Optional[str]:
    """事件流鉴权：等客户端发来首条 {"type":"auth","token":...}。

    令牌不放查询串（会进访问日志），所以走带内握手；超时按未授权处理。
    """
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=timeout)
    except Exception:
        return None
    try:
        msg = json.loads(raw)
    except Exception:
        return None
    if not isinstance(msg, dict) or msg.get("type") != "auth":
        return None
    tok = msg.get("token")
    return tok if isinstance(tok, str) and tok else None


@router.websocket("/ws/pose")
async def ws_pose(ws: WebSocket):
    if not _origin_ok(ws):
        await _deny(ws, 1008, "origin not allowed")
        return
    if hub.count("pose") >= MAX_CLIENTS_PER_STREAM:
        await _deny(ws, 1013, "too many pose clients")
        return
    await ws.accept()
    loop = asyncio.get_running_loop()
    hub.register(ws, loop, "pose")
    try:
        # 连接即刻推送一帧当前快照（姿态 + rc-status，新客户端不等下一广播周期）
        snap = collector.get_latest()
        if snap:
            await ws.send_json(snap)
        rc = getattr(collector, "latest_rc_status", None)
        if rc:
            await ws.send_json(rc)
        # 保持连接: 阻塞等待客户端消息/断开 (广播由 hub 异步推送)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.unregister(ws)


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """审计事件流（★ 需控制令牌）。

    原实现事件帧混在 `/ws/pose` 里广播给所有匿名连接 —— 登录失败、操作者、
    IP 全都外泄。现在单独成流：先过 Origin + 上限，再要令牌。
    前端未登录时订阅失败会自动退回轮询（EventsView 的既有行为）。
    """
    if not _origin_ok(ws):
        await _deny(ws, 1008, "origin not allowed")
        return
    if hub.count("events") >= MAX_CLIENTS_PER_STREAM:
        await _deny(ws, 1013, "too many event clients")
        return
    await ws.accept()
    tok = await _wait_token(ws)
    if not valid_token(tok):
        await _deny(ws, 4401, "control token required")
        return
    loop = asyncio.get_running_loop()
    hub.register(ws, loop, "events")
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.unregister(ws)
