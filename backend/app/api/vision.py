# -*- coding: utf-8 -*-
"""
视觉颜色分拣 API（后端侧：代理相机服务 + 记录落库 + 规则联动）。

分工：
  算法与实时画面在相机服务(:8100)；本模块只负责
    - 把相机服务的状态/配置/标定透出来（代理，前端不必直连两个端口）
    - 把分拣结果落库并提供查询/统计/导出/复核
    - 维护"颜色 → 程序"规则，并在开启时联动执行（多重开关见 services/vision_rules.py）

权限（沿用项目约定）：
  读接口（状态/记录/统计/导出/图片/规则查询）全公开；
  改配置/切背景/一键标定/规则变更 需管理员令牌。

端点一览（前端 apiUrl() 会自动补 /api 前缀）：
  GET    /vision/status                相机服务 + 摄入线程状态
  GET    /vision/last                  最近一次触发（透传相机）
  GET    /vision/records               记录列表（DB，支持颜色/时间过滤 + 分页）
  GET    /vision/stats                 按颜色统计
  GET    /vision/export                导出 json / csv
  GET    /vision/image?p=              取存档图（代理相机服务，带目录穿越防护）
  GET    /vision/rules                 规则表
  GET    /vision/programs              可选程序清单
  GET    /vision/card                  色卡 + 当前标定概览
  POST   /vision/config                更新相机侧配置           [admin]
  POST   /vision/background            固化/清除静态背景        [admin]
  POST   /vision/calibrate/gray        灰卡白平衡               [admin]
  POST   /vision/calibrate/sample      实物采样标定             [admin]
  PUT    /vision/rules                 保存规则表               [admin]
  POST   /vision/records/{rid}/review  人工复核（改判 / 并入色卡）[admin]
  DELETE /vision/records               清理记录（?days=N）      [admin]
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import require_admin, token_role
from app.core.deps import get_db
from app.db import crud
from app.services import camera_client
from app.services.events import emit as emit_event
from app.services.events import since_dt
from app.services.vision_ingest import ingest
from app.services.vision_rules import load_rules, programs_brief, save_rules

router = APIRouter(prefix="/api/vision", tags=["vision"])

_EXPORT_FORMAT = "efort-vision/v1"
COLORS_HINT = ("红 橙 黄 草绿 深绿 青 天蓝 蓝 深蓝 紫 粉 棕 金 米白 白 银灰 灰 黑")


def _iso(dt) -> str:
    try:
        if dt is None:
            return ""
        return dt.isoformat() + "Z" if dt.tzinfo is None else dt.isoformat()
    except Exception:
        return ""


def _row(r) -> dict:
    try:
        images = json.loads(r.images or "{}")
    except Exception:
        images = {}
    return {
        "id": r.id, "seq": r.seq, "timestamp": _iso(r.timestamp),
        "color": r.color, "hex": r.hex, "conf": r.conf, "de": r.de,
        "lab": r.lab, "ratio": r.ratio, "alt": r.alt, "center": r.center,
        "area": r.area, "reason": r.reason, "images": images,
        "program_id": r.program_id, "program_name": r.program_name,
        "outcome": r.outcome, "note": r.note, "source": r.source,
    }


# ============================ 状态 / 代理 ============================
@router.get("/status")
def api_status():
    """相机服务状态 + 摄入状态（前端一个接口拿全）。"""
    st, cam, err = camera_client.get_json("/vision/status", timeout=5.0)
    return {
        "camera": cam if (st == 200 and not err) else None,
        "camera_ok": st == 200 and not err,
        "camera_error": err or "",
        "base_url": camera_client.base_url(),
        "ingest": ingest.status(),
    }


@router.get("/last")
def api_last(since: int = Query(0, ge=0)):
    st, j, err = camera_client.get_json("/vision/last?since=%d" % since, timeout=2.0)
    if st != 200 or err:
        return {"ok": False, "error": err or "相机服务不可用", "event": None, "seq": 0}
    return {"ok": True, "event": (j or {}).get("event"), "seq": (j or {}).get("seq", 0)}


@router.get("/image")
def api_image(p: str = Query(..., description="相对 VISION_DIR 的路径（来自记录的 images 字段）")):
    """代理相机服务的存档图。★ 目录穿越由相机服务侧统一拦截，这里只做透传。"""
    from urllib.parse import quote
    st, data, err = camera_client.get_binary("/vision/image?p=" + quote(p, safe="/"))
    if st != 200 or not isinstance(data, (bytes, bytearray)):
        raise HTTPException(404, detail=err or "图片不存在")
    ctype = "image/png" if p.lower().endswith(".png") else "image/jpeg"
    return StreamingResponse(iter([bytes(data)]), media_type=ctype)


@router.get("/card")
def api_card():
    """色卡 + 当前标定（前端标定面板用）。"""
    st, cam, err = camera_client.get_json("/vision/status", timeout=5.0)
    if st != 200 or err or not cam:
        return {"ok": False, "error": err or "相机服务不可用", "card": {}, "calib": {}}
    return {"ok": True, "card": cam.get("card") or {},
            "achromatic": cam.get("achromatic") or [],
            "calib": cam.get("calib") or {},
            "hint": COLORS_HINT}


# ============================ 记录 / 统计 / 导出 ============================
@router.get("/records")
def api_records(
    color: Optional[str] = Query(None),
    hours: Optional[float] = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    since = since_dt(hours)
    rows = crud.list_vision_records(db, color=color, since=since, limit=limit, offset=offset)
    return {
        "items": [_row(r) for r in rows],
        "total": crud.count_vision_records(db, color=color, since=since),
        "stats": crud.vision_stats(db, since),
        "limit": limit, "offset": offset,
    }


@router.get("/stats")
def api_stats(hours: Optional[float] = Query(None, ge=0), db: Session = Depends(get_db)):
    return crud.vision_stats(db, since_dt(hours))


@router.get("/export")
def api_export(
    format: str = Query("csv", description="json | csv"),
    color: Optional[str] = None,
    hours: Optional[float] = None,
    limit: int = Query(5000, ge=1, le=50000),
    db: Session = Depends(get_db),
):
    rows = crud.list_vision_records(db, color=color, since=since_dt(hours),
                                    limit=limit, offset=0)
    items = [_row(r) for r in rows]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "time", "color", "hex", "conf", "de", "lab", "ratio",
                    "alt", "center", "area", "reason", "outcome",
                    "program_name", "note", "full_image"])
        for it in items:
            w.writerow([it["id"], it["timestamp"], it["color"], it["hex"],
                        it["conf"], it["de"], it["lab"], it["ratio"], it["alt"],
                        it["center"], it["area"], it["reason"], it["outcome"],
                        it["program_name"], it["note"],
                        (it["images"] or {}).get("full", "")])
        payload = buf.getvalue().encode("utf-8-sig")     # Excel 直接打开不乱码
        return StreamingResponse(
            iter([payload]), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     "attachment; filename=efort_vision_%s.csv" % stamp})

    body = {
        "format": _EXPORT_FORMAT,
        "exported_at": datetime.now(timezone.utc).isoformat() + "Z",
        "filters": {"color": color, "hours": hours},
        "count": len(items),
        "stats": crud.vision_stats(db, since_dt(hours)),
        "items": items,
    }
    return StreamingResponse(
        iter([json.dumps(body, ensure_ascii=False, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition":
                 "attachment; filename=efort_vision_%s.json" % stamp})


@router.delete("/records")
def api_clear_records(
    days: Optional[float] = Query(None, ge=0, description="只清理 N 天前；不传为全清"),
    tok: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    n = crud.prune_vision_records(db, int(days or 0))
    emit_event("vision", "warn" if not days else "info", "vision.records_clear",
               "视觉记录清理：删除 %d 条（days=%s）" % (n, days or 0),
               {"deleted": n, "days": days or 0}, actor=token_role(tok) or "admin")
    return {"ok": True, "deleted": n}


class ReviewIn(BaseModel):
    color: Optional[str] = Field(None, description="人工改判后的颜色；不传则只标记已复核")
    teach: bool = Field(False, description="是否把该样本的实测 Lab 并入色卡（自学习）")
    note: Optional[str] = None


@router.post("/records/{rid}/review")
def api_review(rid: int, body: ReviewIn, tok: str = Depends(require_admin),
               db: Session = Depends(get_db)):
    """人工复核：低置信/未识别样本点一下就归好类，并可自动并入色卡。

    ★ 这是"越用越准"的关键：现场实物标定比任何标准色卡都贴合实际。
    """
    row = crud.get_vision_record(db, rid)
    if row is None:
        raise HTTPException(404, detail="记录不存在")
    fields = {"outcome": "reviewed", "source": "review"}
    if body.color:
        fields["color"] = str(body.color)[:16]
    if body.note is not None:
        fields["note"] = str(body.note)[:255]
    row = crud.update_vision_record(db, rid, **fields)

    taught = None
    if body.teach:
        lab = None
        try:
            lab = [float(x) for x in (row.lab or "").split(",")]
        except Exception:
            lab = None
        if lab and len(lab) == 3:
            st, j, err = camera_client.post_json(
                "/vision/calibrate/sample",
                {"name": body.color or row.color, "lab": lab}, timeout=3.0)
            taught = bool(st == 200 and not err and (j or {}).get("ok"))
        else:
            taught = False

    emit_event("vision", "info", "vision.review",
               "视觉记录复核：#%s → %s" % (rid, fields.get("color") or row.color),
               {"id": rid, "color": fields.get("color") or row.color,
                "teach": bool(body.teach), "taught": taught},
               actor=token_role(tok) or "admin")
    out = _row(row)
    out["taught"] = taught
    return out


# ============================ 配置 / 标定（代理）============================
class VisionConfigIn(BaseModel):
    enabled: Optional[bool] = None
    roi: Optional[list] = None
    use_mog2: Optional[bool] = None
    min_interval: Optional[float] = None
    save_on_detect: Optional[bool] = None
    reset_background: Optional[bool] = None
    # ★ 审计修复 P0-cam-3: 三色置信度阈值 + 检测阈值表(部分覆盖)，
    #   以前后端这条路是断的 —— 只能在相机服务上手改/手发，现场没法从界面调。
    #   透传给相机服务，未知键由相机侧 norm_thr() 丢弃并限幅，不会因为脏值停摆。
    min_conf: Optional[float] = Field(None, ge=0.0, le=1.0,
                                      description="三色置信度阈值(0~1)")
    thr: Optional[dict] = Field(None, description="检测阈值部分覆盖，如 {min_area_ratio:0.003}")


@router.post("/config")
def api_set_config(body: VisionConfigIn, tok: str = Depends(require_admin)):
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    st, j, err = camera_client.post_json("/vision/config", payload, timeout=3.0)
    if st != 200 or err:
        raise HTTPException(502, detail=err or "相机服务不可用")
    emit_event("config", "info", "vision.config",
               "视觉分拣配置更新：%s" % (", ".join("%s=%s" % kv for kv in payload.items()) or "无"),
               {"changed": payload}, actor=token_role(tok) or "admin")
    return {"ok": True, "camera": (j or {}).get("status")}


class BackgroundIn(BaseModel):
    reset: bool = False


@router.post("/background")
def api_background(body: BackgroundIn, tok: str = Depends(require_admin)):
    st, j, err = camera_client.post_json("/vision/background", {"reset": body.reset},
                                         timeout=3.0)
    if st != 200 or err:
        raise HTTPException(502, detail=err or "相机服务不可用")
    emit_event("config", "info", "vision.background",
               "静态背景%s" % ("已清除" if body.reset else "已固化"),
               actor=token_role(tok) or "admin")
    return {"ok": True, "message": (j or {}).get("message", ""),
            "camera": (j or {}).get("status")}


class GrayIn(BaseModel):
    target: float = Field(118.0, ge=20.0, le=240.0)


@router.post("/calibrate/gray")
def api_calib_gray(body: GrayIn, tok: str = Depends(require_admin)):
    st, j, err = camera_client.post_json("/vision/calibrate/gray",
                                         {"target": body.target}, timeout=4.0)
    if st != 200 or err or not (j or {}).get("ok"):
        raise HTTPException(400, detail=(j or {}).get("message") or err or "标定失败")
    emit_event("config", "info", "vision.calib_gray",
               "视觉灰卡白平衡标定完成", {"gains": (j or {}).get("info")},
               actor=token_role(tok) or "admin")
    return {"ok": True, "info": (j or {}).get("info")}


class SampleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=16)
    lab: Optional[list] = None


@router.post("/calibrate/sample")
def api_calib_sample(body: SampleIn, tok: str = Depends(require_admin)):
    st, j, err = camera_client.post_json("/vision/calibrate/sample",
                                         {"name": body.name, "lab": body.lab}, timeout=3.0)
    if st != 200 or err or not (j or {}).get("ok"):
        raise HTTPException(400, detail=(j or {}).get("message") or err or "标定失败")
    emit_event("config", "info", "vision.calib_sample",
               "视觉实物标定：%s" % body.name, {"info": (j or {}).get("info")},
               actor=token_role(tok) or "admin")
    return {"ok": True, "info": (j or {}).get("info")}


# ============================ 规则 ============================
@router.get("/rules")
def api_get_rules():
    return {"rules": load_rules().get("rules") or [],
            "auto_execute": ingest.status().get("auto_execute", False),
            "programs": programs_brief(),
            "hint": "颜色→程序；只有 全局开关(config.vision.auto_execute) 与 规则勾选 同时为真才会真正下发。"}


class RulesIn(BaseModel):
    rules: list = Field(default_factory=list)


@router.put("/rules")
def api_put_rules(body: RulesIn, tok: str = Depends(require_admin)):
    norm = save_rules({"rules": body.rules})
    emit_event("config", "warn" if len(norm["rules"]) != len(body.rules) else "info",
               "vision.rules_save",
               "视觉分拣规则已保存（%d 条）" % len(norm["rules"]),
               {"rules": norm["rules"]}, actor=token_role(tok) or "admin")
    return {"ok": True, "rules": norm["rules"]}
