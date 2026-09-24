# -*- coding: utf-8 -*-
"""
运动坐标系定义：`config/frames.json`（与 safety.json 同构：纯 JSON、原子写、强校验）。

## 官方语义（EFORT 示教器，官网机器人学院 + ER 系列操作手册）

坐标系切换顺序：**关节 → 机器人 → 工具 → 用户**，切换结果显示在状态栏。

| 坐标系 | 原点/轴向 | 轴按钮 | 步长单位 |
|---|---|---|---|
| 关节 joint | 各关节自身 | 1±~6± = J1~J6 | 度 |
| 机器人 base | **底座中心**；Z 竖直向上；X 由**底座航插对向**确定 | 1±=X 2±=Y 3±=Z 4±=A 5±=B 6±=C | mm（A/B/C 为度）|
| 工具 tool | 当前激活 **TCP** | 同机器人系 | mm / 度 |
| 用户 user | 用户自定义，一般取在工件上 | 同机器人系 | mm / 度 |

★ **`wobj0` 就是机器人坐标系**（官方明文：wobj0 是系统默认用户坐标系，即机器人坐标系）。
因此本模块**不单独实现 base**：`base` 只是"基座参数 + wobj0"的组合，两者共用同一条求解路径，
少一整份代码，也避免两边参数不一致。

★ 轴 4/5/6 在直角系下是**旋转**（单位 deg），1/2/3 是**平移**（单位 mm）——
单位是**按轴**决定的，不是按坐标系，这一点前后端必须一致。

## 未标定项

`base.x_axis_yaw_deg` 无法从配置推导（取决于底座航插朝向），默认 0 且 `verified: false`。
未标定时**不禁止**点动（否则真机联调前完全没法用），但接口会回 `verified` 让前端标注
"方向未标定，仅供参考"。真机实测后填上即可。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from app.core.config import project_root

FRAMES_PATH = os.path.join(project_root(), "config", "frames.json")

# wobj0 保留给机器人坐标系，用户坐标系用 wobj1~wobj5（与官方一致）
BUILTIN_USER = "wobj0"
USER_IDS = ["wobj0", "wobj1", "wobj2", "wobj3", "wobj4", "wobj5"]

# 轴名（直角系，官方：1±=X 2±=Y 3±=Z 4±=A 5±=B 6±=C）
CART_AXES = ["X", "Y", "Z", "A", "B", "C"]
JOINT_AXES = ["J1", "J2", "J3", "J4", "J5", "J6"]

FRAME_IDS = ["joint", "base", "tool", "user"]


class FramesError(ValueError):
    """坐标系配置非法（消息直接展示给用户）。"""


# ---------------------------------------------------------------------------
# 默认 / 校验
# ---------------------------------------------------------------------------
def default_frames() -> Dict[str, Any]:
    return {
        "version": 1,
        "base": {
            "name": "机器人坐标系",
            # ★ 需真机实测：X 轴在水平面内的偏航（相对 DH 基座系）
            "x_axis_yaw_deg": 0.0,
            "z_sign": 1,
            "verified": False,
        },
        "tool": {
            "name": "工具坐标系",
            # TCP 相对法兰的偏移（mm）。★ 当前项目 TCP 只有位置没有姿态，
            # 所以工具系与法兰系**姿态相同**（rpy 保留字段，暂不参与求解）。
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "rx": 0.0,
            "ry": 0.0,
            "rz": 0.0,
            "verified": False,
            "note": "姿态 rpy 暂未参与求解（工具系姿态 = 法兰姿态）",
        },
        "wobj": [
            {
                "id": "wobj0",
                "name": "机器人坐标系（默认）",
                "x": 0.0, "y": 0.0, "z": 0.0,
                "rx": 0.0, "ry": 0.0, "rz": 0.0,
                "verified": True,
                "builtin": True,
            }
        ],
    }


def _num(v: Any, lo: float, hi: float, field: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise FramesError(f"{field} 必须是数字")
    f = float(v)
    if f != f or f < lo or f > hi:
        raise FramesError(f"{field} 必须在 {lo} ~ {hi} 之间（收到 {v}）")
    return f


def _wobj(item: Any, idx: int) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise FramesError(f"wobj[{idx}] 必须是对象")
    wid = str(item.get("id") or "").strip()
    if wid not in USER_IDS:
        raise FramesError(f"wobj[{idx}].id 必须是 {USER_IDS[0]}~{USER_IDS[-1]} 之一（收到 {wid or '空'}）")
    name = str(item.get("name") or wid).strip()[:32]
    return {
        "id": wid,
        "name": name or wid,
        "x": _num(item.get("x", 0.0), -10000.0, 10000.0, f"{wid}.x"),
        "y": _num(item.get("y", 0.0), -10000.0, 10000.0, f"{wid}.y"),
        "z": _num(item.get("z", 0.0), -10000.0, 10000.0, f"{wid}.z"),
        "rx": _num(item.get("rx", 0.0), -360.0, 360.0, f"{wid}.rx"),
        "ry": _num(item.get("ry", 0.0), -360.0, 360.0, f"{wid}.ry"),
        "rz": _num(item.get("rz", 0.0), -360.0, 360.0, f"{wid}.rz"),
        "verified": bool(item.get("verified", False)),
        "builtin": wid == BUILTIN_USER,
    }


def validate_frames(cfg: Any) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        raise FramesError("配置必须是对象")
    out: Dict[str, Any] = {"version": 1}

    b = cfg.get("base") or {}
    if not isinstance(b, dict):
        raise FramesError("base 必须是对象")
    out["base"] = {
        "name": str(b.get("name") or "机器人坐标系")[:32],
        "x_axis_yaw_deg": _num(b.get("x_axis_yaw_deg", 0.0), -360.0, 360.0, "base.x_axis_yaw_deg"),
        "z_sign": int(_num(b.get("z_sign", 1), -1.0, 1.0, "base.z_sign")) or 1,
        "verified": bool(b.get("verified", False)),
    }

    t = cfg.get("tool") or {}
    if not isinstance(t, dict):
        raise FramesError("tool 必须是对象")
    out["tool"] = {
        "name": str(t.get("name") or "工具坐标系")[:32],
        "x": _num(t.get("x", 0.0), -2000.0, 2000.0, "tool.x"),
        "y": _num(t.get("y", 0.0), -2000.0, 2000.0, "tool.y"),
        "z": _num(t.get("z", 0.0), -2000.0, 2000.0, "tool.z"),
        "rx": _num(t.get("rx", 0.0), -360.0, 360.0, "tool.rx"),
        "ry": _num(t.get("ry", 0.0), -360.0, 360.0, "tool.ry"),
        "rz": _num(t.get("rz", 0.0), -360.0, 360.0, "tool.rz"),
        "verified": bool(t.get("verified", False)),
        "note": str(t.get("note") or "")[:120],
    }

    ws = cfg.get("wobj") or []
    if not isinstance(ws, list) or not ws:
        raise FramesError("至少需要一个用户坐标系（wobj0 为机器人坐标系）")
    if len(ws) > len(USER_IDS):
        raise FramesError(f"用户坐标系最多 {len(USER_IDS)} 个")
    parsed: List[Dict[str, Any]] = []
    seen = set()
    for i, item in enumerate(ws):
        w = _wobj(item, i)
        if w["id"] in seen:
            raise FramesError(f"用户坐标系 id 重复：{w['id']}")
        seen.add(w["id"])
        parsed.append(w)

    # wobj0 必须存在且恒等于机器人坐标系（原点/零姿态），保证"机器人坐标系"始终可用
    w0 = next((w for w in parsed if w["id"] == BUILTIN_USER), None)
    if w0 is None:
        parsed.insert(0, _wobj({"id": BUILTIN_USER, "name": "机器人坐标系（默认）",
                                "verified": True}, 0))
    else:
        for k in ("x", "y", "z", "rx", "ry", "rz"):
            w0[k] = 0.0
        w0["verified"] = True
        w0["builtin"] = True

    out["wobj"] = parsed
    return out


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def load_frames() -> Dict[str, Any]:
    """读取配置；缺失/损坏回退默认并写回（自愈，与 safety.json 同策略）。"""
    try:
        with open(FRAMES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        norm = validate_frames(raw)
        if norm != raw:
            try:
                save_frames(norm)
            except OSError:
                pass
        return norm
    except (OSError, json.JSONDecodeError, FramesError):
        cfg = default_frames()
        try:
            save_frames(cfg)
        except OSError:
            pass
        return cfg


def save_frames(cfg: Any) -> Dict[str, Any]:
    norm = validate_frames(cfg)
    os.makedirs(os.path.dirname(FRAMES_PATH), exist_ok=True)
    tmp = FRAMES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FRAMES_PATH)
    return norm


def reset_frames() -> Dict[str, Any]:
    return save_frames(default_frames())


def get_wobj(cfg: Dict[str, Any], wid: Optional[str]) -> Optional[Dict[str, Any]]:
    if not wid:
        return None
    return next((w for w in cfg.get("wobj", []) if w["id"] == wid), None)


# ---------------------------------------------------------------------------
# 描述（给前端 + 给点动引擎）
# ---------------------------------------------------------------------------
def _unit_of_axis(kind: str, axis: int) -> str:
    """★ 单位是**按轴**决定的：关节系恒为度；直角系 1~3 平移(mm)、4~6 旋转(deg)。"""
    if kind == "joint":
        return "deg"
    return "mm" if axis <= 3 else "deg"


def frame_descriptor(frame: str, user_id: Optional[str] = None,
                     cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """返回某个坐标系的描述（轴名/单位/标定状态），供接口回给前端。"""
    cfg = cfg or load_frames()
    frame = frame if frame in FRAME_IDS else "joint"
    if frame == "joint":
        return {
            "id": "joint", "label": "关节", "kind": "joint",
            "axes": list(JOINT_AXES), "unit": "deg", "verified": True,
            "verified_hint": "",
        }
    if frame == "base":
        b = cfg["base"]
        return {
            "id": "base", "label": "机器人", "kind": "cartesian",
            "axes": list(CART_AXES), "unit": "mm",
            "verified": bool(b.get("verified")),
            "verified_hint": "" if b.get("verified") else "基座 X 轴朝向未标定（需真机实测航插对向），方向仅供参考",
        }
    if frame == "tool":
        t = cfg["tool"]
        return {
            "id": "tool", "label": "工具", "kind": "cartesian",
            "axes": list(CART_AXES), "unit": "mm",
            "tcp": {"x": t["x"], "y": t["y"], "z": t["z"]},
            "verified": bool(t.get("verified")),
            "verified_hint": "" if t.get("verified") else "工具 TCP 未标定，工具系原点暂用配置值",
        }
    w = get_wobj(cfg, user_id or BUILTIN_USER)
    if w is None:
        raise FramesError(f"用户坐标系不存在：{user_id}")
    return {
        "id": "user", "label": f"用户 · {w['name']}", "kind": "cartesian",
        "axes": list(CART_AXES), "unit": "mm",
        "user_id": w["id"], "user_name": w["name"],
        "pose": {k: w[k] for k in ("x", "y", "z", "rx", "ry", "rz")},
        "verified": bool(w.get("verified")),
        "verified_hint": "" if w.get("verified") else f"{w['name']} 未标定，方向仅供参考",
    }


def describe() -> Dict[str, Any]:
    """给设置界面/坐标系列表用的完整描述。"""
    cfg = load_frames()
    return {
        "config": cfg,
        "frames": [
            {"id": "joint", "label": "关节", "kind": "joint", "axes": list(JOINT_AXES),
             "unit": "deg", "verified": True, "hint": "各关节独立转动，适合大范围移动与脱离奇异点"},
            {"id": "base", "label": "机器人", "kind": "cartesian", "axes": list(CART_AXES),
             "unit": "mm", "verified": cfg["base"]["verified"],
             "hint": "沿机器人基座 X/Y/Z 平移、绕 A/B/C 旋转（A 绕 Z、B 绕 Y、C 绕 X）"},
            {"id": "tool", "label": "工具", "kind": "cartesian", "axes": list(CART_AXES),
             "unit": "mm", "verified": cfg["tool"]["verified"],
             "hint": "沿工具自身轴进给；原点为当前 TCP"},
        ] + [
            {"id": "user", "user_id": w["id"], "label": f"用户 · {w['name']}",
             "kind": "cartesian", "axes": list(CART_AXES), "unit": "mm",
             "verified": w["verified"], "builtin": w["builtin"],
             "hint": "沿工件坐标系移动；wobj0 即机器人坐标系" if w["builtin"]
                     else "沿该工件坐标系移动"}
            for w in cfg["wobj"]
        ],
        "path": FRAMES_PATH,
    }


def axis_names(frame: str, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    if frame == "joint":
        return list(JOINT_AXES)
    return list(CART_AXES)


def unit_of_axis(frame: str, axis: int, cfg: Optional[Dict[str, Any]] = None) -> str:
    kind = "joint" if frame == "joint" else "cartesian"
    return _unit_of_axis(kind, int(axis))
