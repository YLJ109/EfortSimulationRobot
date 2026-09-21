# -*- coding: utf-8 -*-
"""
集中配置加载: 读取项目根目录 config/robot.yaml
所有模块通过 get_config() 获取统一配置, 不散落硬编码常量。
"""
from __future__ import annotations

import os
from typing import Any, Dict

import yaml

# backend/app/core/config.py -> 上溯 3 级到项目根 (core -> app -> backend -> 项目根)
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config", "robot.yaml")


def _load_dotenv(path: str | None = None) -> int:
    """读取项目根目录 .env, 写入 os.environ（零依赖, 不覆盖已有变量）。

    规则与常见约定一致:
      - 已存在的真实环境变量优先, .env 不覆盖它;
      - 支持 `#` 整行注释与 `KEY=VALUE` 行;
      - 值两端成对的单/双引号会被剥掉;
      - 文件不存在则静默跳过(所有配置项都有内置默认值)。
    返回实际写入的变量个数, 供启动日志使用。
    """
    p = path or os.path.join(_PROJECT_ROOT, ".env")
    if not os.path.isfile(p):
        return 0
    n = 0
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if not key or key in os.environ:
                    continue
                os.environ[key] = val
                n += 1
    except Exception:
        return n
    return n


dotenv_loaded = _load_dotenv()


def project_root() -> str:
    return _PROJECT_ROOT


def data_dir() -> str:
    d = os.path.join(_PROJECT_ROOT, "data")
    os.makedirs(d, exist_ok=True)
    return d


def db_path() -> str:
    """返回 SQLite 文件绝对路径 (data/robot.db), 目录自动创建。"""
    return os.path.join(data_dir(), "robot.db")


def logs_dir() -> str:
    d = os.path.join(_PROJECT_ROOT, "logs")
    os.makedirs(d, exist_ok=True)
    return d


def public_dir() -> str:
    """前端构建产物目录 (供 FastAPI 静态托管)。"""
    return os.path.join(_PROJECT_ROOT, "frontend", "dist")


class Config:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw

    def get(self, *keys, default=None):
        cur = self.raw
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    # 便捷访问
    @property
    def robot_model(self) -> str:
        return self.get("robot", "model", default="ER8-700H")

    @property
    def modbus(self) -> Dict[str, Any]:
        return self.get("modbus", default={})

    @property
    def connection(self) -> Dict[str, Any]:
        conn = dict(self.get("connection", default={}) or {})
        sim = os.environ.get("EFORT_SIMULATE")
        if sim in ("always", "never", "auto"):
            conn["simulate"] = sim
        return conn

    @property
    def dh(self) -> Dict[str, Any]:
        return self.get("dh", default={})

    @property
    def mounting(self) -> Dict[str, Any]:
        return self.get("mounting", default={})

    @property
    def sampling(self) -> Dict[str, Any]:
        return self.get("sampling", default={})

    @property
    def server(self) -> Dict[str, Any]:
        return self.get("server", default={})


_cfg: Config | None = None


def load_config(path: str = CONFIG_PATH) -> Config:
    global _cfg
    with open(path, "r", encoding="utf-8") as f:
        _cfg = Config(yaml.safe_load(f))
    return _cfg


def get_config() -> Config:
    if _cfg is None:
        return load_config()
    return _cfg
