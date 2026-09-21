# -*- coding: utf-8 -*-
"""WebSocket 实时姿态推送。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.collector import collector
from app.services.hub import hub

router = APIRouter()


@router.websocket("/ws/pose")
async def ws_pose(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    hub.register(ws, loop)
    try:
        # 连接即刻推送一帧当前快照
        snap = collector.get_latest()
        if snap:
            await ws.send_json(snap)
        # 保持连接: 阻塞等待客户端消息/断开 (广播由 hub 异步推送)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.unregister(ws)
