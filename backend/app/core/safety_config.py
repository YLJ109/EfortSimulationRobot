# -*- coding: utf-8 -*-
"""
安全围栏配置：读取/校验/持久化 到 项目根 config/safety.json。

与 robot.yaml 同级，纯 JSON，便于手工编辑与备份；不改动数据库 schema。

配置结构（zones 支持多区域，每区域可独立设形状与阈值）：
{
  "version": 1,
  "enabled": true,
  "zones": [ { id, name, enabled, shape(rect|quad|circle),
               center{x,z}, half{x,z}, radius, corners[[x,z]x4],
               height, walls, posts{enabled,size,color},
               thresholds{basis,warn,danger,fixed_mm},
               colors{safe,warn,danger,hit}, opacity{...} } ],
  "blink": {hz, min, max},
  "alarm": {banner, chip, banner_min, sound},
  "walls": bool,                            # 全局：四面透明玻璃显示开关
  "ground": {enabled, warn_mm, danger_mm, hit_mm, colors, opacity},
                                            # 地面碰撞检测：J1 以上子树最低点离地高度，
                                            # 四级 绿(安全)/黄(接近)/红(危险)/红闪(碰撞)。
                                            # ★ 报警垫形状 = 区域多边形（四个角柱围成的地面），
                                            #   所以没有 radius 字段。
  "overlay": {bbox},
  "camera": {position[3], target[3]}
}
"""
from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, Dict, List

from app.core.config import project_root

SAFETY_PATH = os.path.join(project_root(), "config", "safety.json")

_SHAPES = ("rect", "quad", "circle")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_safety() -> Dict[str, Any]:
    """出厂默认：一个 1.64m 见方的矩形主工作区（贴着机器人可达边界）。"""
    return {
        "version": 1,
        "enabled": True,
        "zones": [
            {
                "id": "z1",
                "name": "主工作区",
                "enabled": True,
                "shape": "rect",
                "center": {"x": 0.0, "z": 0.0},
                "half": {"x": 0.82, "z": 0.82},
                "radius": 0.9,
                "corners": [
                    [-0.82, -0.82],
                    [0.82, -0.82],
                    [0.82, 0.82],
                    [-0.82, 0.82],
                ],
                "height": 1.2,
                "walls": True,
                "posts": {"enabled": True, "size": 0.1, "color": "#f2c500"},
                "thresholds": {
                    "basis": "halfwidth",
                    "warn": 0.30,
                    "danger": 0.10,
                    "fixed_mm": 300,
                },
                "colors": {
                    "safe": "#2ecc71",
                    "warn": "#f2c500",
                    "danger": "#e5484d",
                    "hit": "#ff2020",
                },
                "opacity": {
                    "safe": 0.11,
                    "warn": 0.20,
                    "danger": 0.30,
                    "hit": 0.45,
                },
            }
        ],
        "blink": {"hz": 4.0, "min": 0.18, "max": 0.63},
        "alarm": {"banner": True, "chip": True, "banner_min": "danger", "sound": False},
        "walls": True,
        "ground": {
            "enabled": True,
            "warn_mm": 150,
            "danger_mm": 80,
            "hit_mm": 30,
            "colors": {
                "safe": "#2ecc71",
                "warn": "#f2c500",
                "danger": "#e5484d",
                "hit": "#ff2020",
            },
            "opacity": {
                "safe": 0.10,
                "warn": 0.22,
                "danger": 0.34,
                "hit": 0.50,
            },
        },
        "overlay": {"bbox": False},
        "camera": {"position": [1.7, 1.35, 1.75], "target": [0.0, 0.45, 0.0]},
    }


class SafetyConfigError(ValueError):
    pass


def _num(v: Any, lo: float, hi: float, field: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SafetyConfigError(f"{field} 必须是数字")
    f = float(v)
    if f != f or f < lo or f > hi:  # NaN 检查
        raise SafetyConfigError(f"{field} 必须在 {lo} ~ {hi} 之间，收到 {v}")
    return f


def _color(v: Any, field: str) -> str:
    if not isinstance(v, str) or not _HEX.match(v):
        raise SafetyConfigError(f"{field} 必须是 #RRGGBB 颜色")
    return v.lower()


def _vec2(v: Any, field: str, lo: float = -10.0, hi: float = 10.0) -> Dict[str, float]:
    if not isinstance(v, dict):
        raise SafetyConfigError(f"{field} 必须是对象")
    return {
        "x": _num(v.get("x"), lo, hi, f"{field}.x"),
        "z": _num(v.get("z"), lo, hi, f"{field}.z"),
    }


def _validate_zone(z: Any, idx: int) -> Dict[str, Any]:
    if not isinstance(z, dict):
        raise SafetyConfigError(f"zones[{idx}] 必须是对象")
    out: Dict[str, Any] = {}

    out["id"] = str(z.get("id") or f"z{idx + 1}")
    out["name"] = str(z.get("name") or f"区域{idx + 1}")
    out["enabled"] = bool(z.get("enabled", True))

    shape = z.get("shape", "rect")
    if shape not in _SHAPES:
        raise SafetyConfigError(f"zones[{idx}].shape 必须是 {_SHAPES} 之一")
    out["shape"] = shape

    out["center"] = _vec2(z.get("center", {"x": 0.0, "z": 0.0}), f"zones[{idx}].center")

    if shape == "rect":
        half = z.get("half", {"x": 0.82, "z": 0.82})
        if not isinstance(half, dict):
            raise SafetyConfigError(f"zones[{idx}].half 必须是对象")
        out["half"] = {
            "x": _num(half.get("x"), 0.2, 3.0, f"zones[{idx}].half.x"),
            "z": _num(half.get("z"), 0.2, 3.0, f"zones[{idx}].half.z"),
        }
    else:
        out["half"] = {"x": 0.82, "z": 0.82}

    if shape == "circle":
        out["radius"] = _num(z.get("radius", 0.9), 0.2, 3.0, f"zones[{idx}].radius")
    else:
        out["radius"] = _num(z.get("radius", 0.9), 0.2, 3.0, f"zones[{idx}].radius")

    if shape == "quad":
        cs = z.get("corners") or []
        if not isinstance(cs, list) or len(cs) != 4:
            raise SafetyConfigError(f"zones[{idx}].corners 必须是 4 个点")
        pts: List[List[float]] = []
        for i, p in enumerate(cs):
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise SafetyConfigError(f"zones[{idx}].corners[{i}] 必须是 [x, z]")
            pts.append(
                [
                    _num(p[0], -10.0, 10.0, f"zones[{idx}].corners[{i}][0]"),
                    _num(p[1], -10.0, 10.0, f"zones[{idx}].corners[{i}][1]"),
                ]
            )
        out["corners"] = pts
    else:
        out["corners"] = z.get("corners") or [
            [-0.82, -0.82],
            [0.82, -0.82],
            [0.82, 0.82],
            [-0.82, 0.82],
        ]

    out["height"] = _num(z.get("height", 1.2), 0.3, 3.0, f"zones[{idx}].height")
    out["walls"] = bool(z.get("walls", True))

    p = z.get("posts", {}) or {}
    out["posts"] = {
        "enabled": bool(p.get("enabled", True)),
        "size": _num(p.get("size", 0.1), 0.05, 0.3, f"zones[{idx}].posts.size"),
        "color": _color(p.get("color", "#f2c500"), f"zones[{idx}].posts.color"),
    }

    t = z.get("thresholds", {}) or {}
    basis = t.get("basis", "halfwidth")
    if basis not in ("halfwidth", "fixed"):
        raise SafetyConfigError(f"zones[{idx}].thresholds.basis 必须是 halfwidth 或 fixed")
    warn = _num(t.get("warn", 0.30), 0.01, 0.99, f"zones[{idx}].thresholds.warn")
    danger = _num(t.get("danger", 0.10), 0.001, 0.98, f"zones[{idx}].thresholds.danger")
    if danger >= warn:
        raise SafetyConfigError(f"zones[{idx}]：危险阈值必须小于接近阈值")
    out["thresholds"] = {
        "basis": basis,
        "warn": warn,
        "danger": danger,
        "fixed_mm": _num(
            t.get("fixed_mm", 300), 50, 2000, f"zones[{idx}].thresholds.fixed_mm"
        ),
    }

    c = z.get("colors", {}) or {}
    out["colors"] = {
        "safe": _color(c.get("safe", "#2ecc71"), f"zones[{idx}].colors.safe"),
        "warn": _color(c.get("warn", "#f2c500"), f"zones[{idx}].colors.warn"),
        "danger": _color(c.get("danger", "#e5484d"), f"zones[{idx}].colors.danger"),
        "hit": _color(c.get("hit", "#ff2020"), f"zones[{idx}].colors.hit"),
    }

    o = z.get("opacity", {}) or {}
    out["opacity"] = {
        "safe": _num(o.get("safe", 0.11), 0.02, 0.9, f"zones[{idx}].opacity.safe"),
        "warn": _num(o.get("warn", 0.20), 0.02, 0.9, f"zones[{idx}].opacity.warn"),
        "danger": _num(o.get("danger", 0.30), 0.02, 0.9, f"zones[{idx}].opacity.danger"),
        "hit": _num(o.get("hit", 0.45), 0.02, 0.9, f"zones[{idx}].opacity.hit"),
    }
    return out


def validate_safety(cfg: Any) -> Dict[str, Any]:
    """校验并规范化配置，非法则抛 SafetyConfigError。"""
    if not isinstance(cfg, dict):
        raise SafetyConfigError("配置必须是对象")
    out: Dict[str, Any] = {}

    out["version"] = 1
    out["enabled"] = bool(cfg.get("enabled", True))

    zones = cfg.get("zones") or []
    if not isinstance(zones, list) or not zones:
        raise SafetyConfigError("至少需要一个区域 zones[0]")
    if len(zones) > 8:
        raise SafetyConfigError("区域数量最多 8 个")
    out["zones"] = [_validate_zone(z, i) for i, z in enumerate(zones)]

    b = cfg.get("blink", {}) or {}
    out["blink"] = {
        "hz": _num(b.get("hz", 4.0), 0.5, 10.0, "blink.hz"),
        "min": _num(b.get("min", 0.18), 0.02, 0.9, "blink.min"),
        "max": _num(b.get("max", 0.63), 0.02, 0.9, "blink.max"),
    }

    a = cfg.get("alarm", {}) or {}
    banner_min = a.get("banner_min", "danger")
    if banner_min not in ("warn", "danger", "hit"):
        raise SafetyConfigError("alarm.banner_min 必须是 warn/danger/hit")
    out["alarm"] = {
        "banner": bool(a.get("banner", True)),
        "chip": bool(a.get("chip", True)),
        "banner_min": banner_min,
        "sound": bool(a.get("sound", False)),
    }

    # 全局四面玻璃开关（关掉后仍保留线框与角柱，区域边界依旧可见）
    out["walls"] = bool(cfg.get("walls", True))

    # 地面碰撞检测：J1 以上子树的最低点高度（四级，与围栏面板同构）
    #   min_y <= hit_mm        → hit（碰撞地面，红闪）
    #   min_y <= danger_mm     → danger（危险，红）
    #   min_y <= warn_mm       → warn（接近地面，黄）
    #   else                   → safe（安全，绿）
    # colors/opacity 供 3D 地面报警垫取色（与围栏面板同套配色）。
    # ★ 报警垫的形状由**区域多边形**决定（四个角柱围成的那块地面），
    #   因此这里不再有 radius —— 老配置里残留的 radius 会被直接忽略。
    g = cfg.get("ground", {}) or {}
    warn_mm = _num(g.get("warn_mm", 150), 0, 1000, "ground.warn_mm")
    danger_mm = _num(g.get("danger_mm", 80), 0, 1000, "ground.danger_mm")
    hit_mm = _num(g.get("hit_mm", 30), 0, 500, "ground.hit_mm")
    if not (hit_mm <= danger_mm <= warn_mm):
        raise SafetyConfigError(
            "地面阈值须满足 hit_mm <= danger_mm <= warn_mm"
        )
    gc = g.get("colors", {}) or {}
    go = g.get("opacity", {}) or {}
    out["ground"] = {
        "enabled": bool(g.get("enabled", True)),
        "warn_mm": warn_mm,
        "danger_mm": danger_mm,
        "hit_mm": hit_mm,
        "colors": {
            "safe": _color(gc.get("safe", "#2ecc71"), "ground.colors.safe"),
            "warn": _color(gc.get("warn", "#f2c500"), "ground.colors.warn"),
            "danger": _color(gc.get("danger", "#e5484d"), "ground.colors.danger"),
            "hit": _color(gc.get("hit", "#ff2020"), "ground.colors.hit"),
        },
        "opacity": {
            "safe": _num(go.get("safe", 0.10), 0.02, 0.9, "ground.opacity.safe"),
            "warn": _num(go.get("warn", 0.22), 0.02, 0.9, "ground.opacity.warn"),
            "danger": _num(go.get("danger", 0.34), 0.02, 0.9, "ground.opacity.danger"),
            "hit": _num(go.get("hit", 0.50), 0.02, 0.9, "ground.opacity.hit"),
        },
    }

    ov = cfg.get("overlay", {}) or {}
    out["overlay"] = {"bbox": bool(ov.get("bbox", False))}

    cam = cfg.get("camera", {}) or {}

    def _vec3(v: Any, field: str) -> List[float]:
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise SafetyConfigError(f"{field} 必须是 3 个数")
        return [_num(x, -50.0, 50.0, f"{field}[{i}]") for i, x in enumerate(v)]

    out["camera"] = {
        "position": _vec3(cam.get("position", [1.7, 1.35, 1.75]), "camera.position"),
        "target": _vec3(cam.get("target", [0.0, 0.45, 0.0]), "camera.target"),
    }
    return out


def load_safety() -> Dict[str, Any]:
    """读取配置；文件缺失/损坏时回退默认并写回。

    ★ 归一化结果若与磁盘原文不同（例如老配置文件里还没有 ground 块），
      立即写回让配置文件**自愈**。否则每次启动都拿着"残缺配置"进渲染层，
      会出现「按状态取色取不到 → 地面报警垫永远是绿的」这类问题。
    """
    try:
        with open(SAFETY_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        norm = validate_safety(raw)
        if norm != raw:
            try:
                save_safety(norm)
            except OSError:
                pass
        return norm
    except (OSError, json.JSONDecodeError, SafetyConfigError):
        cfg = default_safety()
        try:
            save_safety(cfg)
        except OSError:
            pass
        return cfg


def save_safety(cfg: Any) -> Dict[str, Any]:
    """校验 + 落盘。"""
    norm = validate_safety(cfg)
    os.makedirs(os.path.dirname(SAFETY_PATH), exist_ok=True)
    tmp = SAFETY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SAFETY_PATH)
    return norm


def reset_safety() -> Dict[str, Any]:
    return save_safety(default_safety())


def clone_default() -> Dict[str, Any]:
    return copy.deepcopy(default_safety())
