# -*- coding: utf-8 -*-
"""
EFORT Web Monitoring 后端入口 (FastAPI)。
启动: 建库 + 启动采集器; 关闭: 停止采集器。
生产: 托管 frontend/dist 静态文件 (SPA)。开发: 用 vite 单独跑前端。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import control, recordings, robot, safety, ws
from app.core.config import get_config, public_dir, db_path, dotenv_loaded
from app.core.exceptions import register_exception_handlers
from app.core.logger import get_logger
from app.core.middleware import RequestLogMiddleware
from app.db.crud import prune_old_poses
from app.db.database import SessionLocal, init_db
from app.services.collector import collector

log = get_logger("main")
if dotenv_loaded:
    log.info("已从 .env 载入 %d 个环境变量", dotenv_loaded)


class NoCacheStaticFiles(StaticFiles):
    """静态资源禁用浏览器缓存 —— 每次重新构建后刷新页面即可看到新版本。"""

    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp


def _prune_history() -> None:
    """按配置保留天数清理历史 (启动时执行一次)。"""
    try:
        days = int(get_config().sampling.get("history_retention_days", 0) or 0)
        if days <= 0:
            return
        s = SessionLocal()
        try:
            n = prune_old_poses(s, days)
            if n:
                log.info("历史清理: 删除 %d 条超过 %d 天的记录", n, days)
        finally:
            s.close()
    except Exception as e:
        log.warning("历史清理失败: %s", e)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    log.info("数据库就绪: %s", db_path())
    _prune_history()
    collector.start()
    yield
    collector.stop()
    log.info("采集器已停止")


app = FastAPI(
    title="EFORT Web Monitoring",
    version="0.2.0",
    description="EFORT ER8-700H 工业机器人全栈 Web 监控与仿真系统",
    lifespan=lifespan,
)

# CORS：同源部署（前端 dist 由本服务托管）+ 开发用 vite proxy，浏览器视角均为同源，
# 故无需携带凭据；通配符 origin 与 allow_credentials=True 是浏览器禁止的非法组合。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求日志 + 全局异常处理
app.add_middleware(RequestLogMiddleware)
register_exception_handlers(app)

app.include_router(robot.router)
app.include_router(ws.router)
app.include_router(recordings.router)
app.include_router(control.router)
app.include_router(safety.router)


# 存活探针/根信息：必须在静态挂载之前注册，否则被 mount("/") 拦截。
@app.get("/healthz")
def healthz():
    """存活探针：不依赖 DB/机器人，仅确认进程存活。"""
    return {"status": "alive"}


@app.get("/api/version")
def api_version():
    """服务版本信息（运维/诊断用）。"""
    return {"service": "EFORT Web Monitoring", "version": "0.2.0", "docs": "/docs", "ws": "/ws/pose"}


# 生产: 托管前端构建产物 (SPA)。必须在 API 路由之后挂载。
# NoCacheStaticFiles: 强制不缓存, 避免"重启后界面无变化"的浏览器缓存问题。
_dist = public_dir()
if os.path.isdir(_dist):
    app.mount("/", NoCacheStaticFiles(directory=_dist, html=True), name="static")
    log.info("已托管前端静态目录(no-cache): %s", _dist)
else:
    log.warning("前端构建目录不存在(%s), 仅提供 API/WS。运行 frontend npm run build 后重试。", _dist)
