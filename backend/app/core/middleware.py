# -*- coding: utf-8 -*-
"""请求日志中间件：记录 method/path/status/耗时。"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logger import get_logger

log = get_logger("middleware")

# 不记录日志的路径（避免噪声）：
# - 静态资源 / MJPEG 流
# - /api/control/ik：机器人模式按键操控的高频调用（按住方向键会连发）
_SKIP_PREFIXES = ("/assets/", "/models/", "/stream", "/api/control/ik")


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        path = request.url.path
        if not path.startswith(_SKIP_PREFIXES):
            log.info("%s %s -> %d (%.1f ms)", request.method, path, response.status_code, elapsed)
        return response
