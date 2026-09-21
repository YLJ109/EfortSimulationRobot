# -*- coding: utf-8 -*-
"""
WebSocket 广播中枢 (线程安全)。

采集线程 (Collector) 在任意线程调用 broadcast(), 经事件循环安全推送到所有客户端。
P1-2 背压: 每客户端只保留"最新一帧"; 客户端发送未完成时, 新帧覆盖旧帧(丢弃积压),
          避免慢客户端导致 asyncio task 无限堆积(延迟/内存上涨)。
"""
from __future__ import annotations

import asyncio
import json

from app.core.logger import get_logger

log = get_logger("hub")


class _Client:
    __slots__ = ("ws", "loop", "latest", "sending")

    def __init__(self, ws, loop) -> None:
        self.ws = ws
        self.loop = loop
        self.latest = None       # 待发的最新帧(字符串), None 表示无积压
        self.sending = False     # 是否已有 drain 任务在跑


class WsHub:
    def __init__(self) -> None:
        self._clients: dict = {}      # ws -> _Client

    def register(self, ws, loop) -> None:
        self._clients[ws] = _Client(ws, loop)

    def unregister(self, ws) -> None:
        self._clients.pop(ws, None)

    def count(self) -> int:
        return len(self._clients)

    def broadcast(self, payload: dict) -> None:
        if not self._clients:
            return
        data = json.dumps(payload, ensure_ascii=False)
        for c in list(self._clients.values()):
            c.latest = data          # 覆盖旧帧 —— 只保留最新
            if c.sending:
                continue
            c.sending = True
            try:
                c.loop.call_soon_threadsafe(self._drain, c)
            except Exception as e:
                log.debug("broadcast skip: %s", e)
                self.unregister(c.ws)

    def _drain(self, c: "_Client") -> None:
        """在事件循环线程内执行: 连续发送直到无积压。"""
        async def _go():
            try:
                while c.latest is not None:
                    data, c.latest = c.latest, None
                    await c.ws.send_text(data)
            except Exception:
                self.unregister(c.ws)
            finally:
                c.sending = False

        asyncio.ensure_future(_go())


hub = WsHub()
