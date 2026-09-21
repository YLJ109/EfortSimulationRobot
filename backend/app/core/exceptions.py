# -*- coding: utf-8 -*-
"""统一业务异常 + 全局异常处理器。

统一错误响应格式: {"code": str, "message": str, "detail": any}
业务代码抛出 AppError; 未捕获异常兜底为 500 并记录日志。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.logger import get_logger

log = get_logger("exceptions")


class AppError(Exception):
    """业务异常：HTTP 状态码 + 业务错误码 + 可读消息 + 可选详情。"""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: str = "BAD_REQUEST",
        detail: Optional[Any] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.detail = detail


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在", detail: Optional[Any] = None):
        super().__init__(message, 404, "NOT_FOUND", detail)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": "HTTP_ERROR", "message": str(exc.detail), "detail": None},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("未捕获异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "detail": str(exc)},
        )
