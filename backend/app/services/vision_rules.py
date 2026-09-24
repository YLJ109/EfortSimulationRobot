# -*- coding: utf-8 -*-
"""
视觉分拣规则：颜色 → 程序 → （可选）自动下发。

安全设计（这块写错就是现场事故，所以规则从三条线同时收口）：
  1. 全局开关 `config.vision.auto_execute`（robot.yaml，默认 false）
  2. 单条规则开关 `rule.auto`（默认 false）
  3. 每个步骤下发前逐条过 `safety_guard.check()` —— 围栏 danger/hit 直接中断

  三者必须同时满足才会真的动。任何一条不满足都只记录"命中规则但未执行"，
  并把原因写进 outcome/note —— 现场最怕的是"以为它在动/以为它没动"。

规则存 config/vision_rules.json（原子写；与 safety.json 同一套约定）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np

from app.core.config import get_config, project_root
from app.core.logger import get_logger
from app.db.crud import get_point, get_program, list_programs
from app.db.database import SessionLocal
from app.services.kinematics import fk_matrix, ikine, rpy_to_matrix, tcp_of
from app.services.motion import motion
from app.services.safety_guard import check as guard_check

log = get_logger("vision_rules")

RULES_PATH = os.path.join(project_root(), "config", "vision_rules.json")

_MAX_RULES = 64


def _rules_path() -> str:
    """允许测试用环境变量重定向（避免污染现场配置）。"""
    p = os.environ.get("EFORT_VISION_RULES")
    return p or RULES_PATH


DEFAULT_RULES: Dict[str, Any] = {"version": 1, "rules": []}


def normalize_rules(raw: Optional[dict]) -> Dict[str, Any]:
    """把任意输入归一化成合法规则表（坏数据丢字段，不抛异常）。"""
    raw = raw if isinstance(raw, dict) else {}
    out_rules: List[dict] = []
    for it in (raw.get("rules") or [])[:_MAX_RULES]:
        if not isinstance(it, dict):
            continue
        color = str(it.get("color") or "").strip()[:16]
        if not color:
            continue
        pid = it.get("program_id")
        try:
            pid = int(pid) if pid not in (None, "", "null") else None
        except Exception:
            pid = None
        try:
            sp = int(it.get("speed_pct") or 100)
        except Exception:
            sp = 100
        out_rules.append({
            "enabled": bool(it.get("enabled", True)),
            "color": color,
            "program_id": pid,
            "program_name": str(it.get("program_name") or "")[:64],
            "speed_pct": max(1, min(100, sp)),
            "auto": bool(it.get("auto", False)),
            "note": str(it.get("note") or "")[:120],
        })
    return {"version": 1, "rules": out_rules}


def load_rules() -> Dict[str, Any]:
    try:
        with open(_rules_path(), "r", encoding="utf-8") as f:
            return normalize_rules(json.load(f))
    except Exception:
        return dict(DEFAULT_RULES)


def save_rules(cfg: Optional[dict]) -> Dict[str, Any]:
    """原子写（先写 .tmp 再 replace），避免半个文件把现场配置写坏。"""
    norm = normalize_rules(cfg)
    path = _rules_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return norm


def rule_for(color: str, rules: Optional[dict] = None) -> Optional[dict]:
    """按颜色找第一条启用中的规则。"""
    rules = rules if rules is not None else load_rules()
    for r in rules.get("rules") or []:
        if r.get("enabled") and r.get("color") == color:
            return r
    return None


def auto_execute_enabled() -> bool:
    return bool(get_config().vision.get("auto_execute", False))


def _limits() -> List[dict]:
    lim = get_config().get("joint_limits", default=[]) or []
    out = []
    for i, it in enumerate(lim[:6]):
        out.append({"name": it.get("name", "J%d" % (i + 1)),
                    "min": float(it.get("min", -360)), "max": float(it.get("max", 360))})
    while len(out) < 6:
        out.append({"name": "J%d" % (len(out) + 1), "min": -360.0, "max": 360.0})
    return out


def _in_limits(q: List[float], limits: List[dict]) -> bool:
    return all(float(limits[i]["min"]) <= float(q[i]) <= float(limits[i]["max"])
               for i in range(6))


def _steps_from_items(items, db) -> List[dict]:
    """把程序条目归一化为可执行步骤（与 api/control._steps_from 同形）。"""
    out: List[dict] = []
    for i, it in enumerate(items or []):
        idx = i + 1
        if not isinstance(it, dict):
            out.append({"index": idx, "ok": False, "error": "步骤格式非法"})
            continue
        sp = int(it.get("speed_pct") or 0) or None
        dw = int(it.get("dwell_ms") or 0)
        pid = it.get("point_id", it.get("point"))
        if pid is not None and str(pid).strip() != "":
            row = get_point(db, int(pid)) if str(pid).isdigit() else None
            if row is None:
                out.append({"index": idx, "ok": False, "error": "点位 #%s 不存在" % pid})
                continue
            try:
                joints = [float(x) for x in json.loads(row.joints or "[0,0,0,0,0,0]")]
            except Exception:
                joints = [0.0] * 6
            st = {"index": idx, "name": row.name, "mode": "joint", "joints": joints,
                  "speed_pct": sp, "dwell_ms": dw}
            if row.kind == "cartesian":
                try:
                    t = json.loads(row.tcp or "null")
                except Exception:
                    t = None
                if isinstance(t, dict):
                    st["mode"] = "cartesian"
                    st["tcp"] = t
            out.append(st)
            continue
        if isinstance(it.get("tcp"), dict):
            out.append({"index": idx, "name": it.get("name") or "步骤%d" % idx,
                        "mode": "cartesian", "tcp": it["tcp"],
                        "speed_pct": sp, "dwell_ms": dw})
        elif isinstance(it.get("joints"), list) and len(it["joints"]) == 6:
            out.append({"index": idx, "name": it.get("name") or "步骤%d" % idx,
                        "mode": "joint", "joints": [float(x) for x in it["joints"]],
                        "speed_pct": sp, "dwell_ms": dw})
        else:
            out.append({"index": idx, "ok": False, "error": "步骤缺少 joints / tcp / point_id"})
    return out


def execute_rule(rule: dict, *, color: str = "", seq: int = 0,
                 actor: str = "vision") -> Dict[str, Any]:
    """执行一条规则指向的程序。返回 {ok, outcome, note, steps, program_id, program_name}。

    outcome ∈ executed | skipped | blocked | error
    """
    res: Dict[str, Any] = {
        "ok": False, "outcome": "skipped", "note": "", "steps": [],
        "program_id": rule.get("program_id"), "program_name": rule.get("program_name") or "",
    }
    if not rule.get("enabled", True):
        res["note"] = "规则已停用"
        return res
    if not auto_execute_enabled():
        res["note"] = "全局自动执行开关(config.vision.auto_execute)为关，仅记录"
        return res
    if not rule.get("auto"):
        res["note"] = "该规则未勾选自动执行，仅记录"
        return res

    pid = rule.get("program_id")
    if not pid:
        res["outcome"] = "error"
        res["note"] = "规则未绑定程序"
        return res

    db = SessionLocal()
    try:
        prog = get_program(db, int(pid))
        if prog is None:
            res["outcome"] = "error"
            res["note"] = "程序 #%s 不存在" % pid
            return res
        res["program_id"] = prog.id
        res["program_name"] = prog.name
        try:
            items = json.loads(prog.items or "[]")
        except Exception:
            items = []
        steps = _steps_from_items(items, db)
        if not steps:
            res["outcome"] = "error"
            res["note"] = "程序为空"
            return res

        limits = _limits()
        speed_pct = int(rule.get("speed_pct") or 100)
        run: List[dict] = []
        ok_all = True
        for st in steps:
            if st.get("ok") is False:
                run.append(st)
                ok_all = False
                break
            # ★ 每步都过围栏互锁：视觉误判造成的"意外启动"在这里被兜住
            okg, reason, _snap = guard_check()
            if not okg:
                run.append({"index": st["index"], "ok": False, "blocked": True,
                            "reason": reason})
                res["outcome"] = "blocked"
                res["note"] = "围栏互锁拦截：%s" % reason
                ok_all = False
                break

            sp = int(st.get("speed_pct") or speed_pct)
            dw = int(st.get("dwell_ms") or 0)
            target = st.get("joints")
            if target is None:
                tcp = st.get("tcp") or {}
                q0 = list(motion.last_target or [0.0] * 6)
                T = np.eye(4)
                T[:3, 3] = [float(tcp.get("x", 0.0)), float(tcp.get("y", 0.0)),
                            float(tcp.get("z", 0.0))]
                pose_given = all(tcp.get(k) is not None for k in ("rx", "ry", "rz"))
                if pose_given:
                    T[:3, :3] = rpy_to_matrix(tcp["rx"], tcp["ry"], tcp["rz"])
                    rw = 150.0
                else:
                    T[:3, :3] = fk_matrix(q0)[:3, :3]
                    rw = 0.0
                r = ikine(T, q0, limits, rot_weight=rw)
                if not r["ok"] or not _in_limits(r["joints"], limits):
                    run.append({"index": st["index"], "ok": False,
                                "error": "IK 解算失败或超限位", "solver": r})
                    ok_all = False
                    break
                target = r["joints"]

            rr = motion.command([float(x) for x in target], sp, dw)
            run.append({"index": st["index"], "name": st.get("name"),
                        "ok": bool(rr.get("ok")), "mode": rr.get("mode"),
                        "target": [round(float(v), 2) for v in target],
                        "error": rr.get("error")})
            if not rr.get("ok"):
                ok_all = False
                break
            time.sleep(max(0.15, dw / 1000.0 + 0.2))

        res["steps"] = run
        res["ok"] = ok_all
        if res["outcome"] != "blocked":
            res["outcome"] = "executed" if ok_all else "error"
            if not ok_all:
                res["note"] = "执行中断（见 steps 中失败步）"
        return res
    except Exception as e:                     # noqa: BLE001 —— 规则执行绝不能让摄入线程挂掉
        log.warning("规则执行异常: %s", e)
        res["outcome"] = "error"
        res["note"] = "执行异常: %s" % str(e)[:120]
        return res
    finally:
        db.close()


def programs_brief() -> List[dict]:
    """给前端规则面板用：可选程序清单（只读）。"""
    db = SessionLocal()
    try:
        out = []
        for p in list_programs(db):
            try:
                n = len(json.loads(p.items or "[]"))
            except Exception:
                n = 0
            out.append({"id": p.id, "name": p.name, "steps": n})
        return out
    finally:
        db.close()


def tcp_of_last() -> Optional[List[float]]:
    """最近一次目标位姿的 TCP（诊断用）。"""
    try:
        return list(tcp_of(list(motion.last_target or [0.0] * 6)))
    except Exception:
        return None
