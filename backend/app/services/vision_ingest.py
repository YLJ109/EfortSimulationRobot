# -*- coding: utf-8 -*-
"""
视觉事件摄入：把相机服务的分拣结果搬进后端（记录 / 事件 / 规则联动）。

为什么用"轮询"而不是"相机服务回调后端":
  相机服务是独立进程、可能先于后端启动、也可能被单独重启；让相机去回调后端会
  引入"谁先起来"的耦合。轮询 `/vision/last?since=seq` 是无状态幂等的，
  两端可以任意顺序启动/重启，最多晚 poll_ms 拿到结果 —— 对 1 秒停留的场景完全够。

★ 必须幂等：后端重启后会拿 since=0 重放，靠 DB 里 seq 的唯一索引兜底，
  否则每次重启都会给最近一次分拣多记一条。
"""
from __future__ import annotations

import threading
import time

from app.core.config import get_config
from app.core.logger import get_logger
from app.db.crud import insert_vision_record, prune_vision_records
from app.db.database import SessionLocal
from app.services import camera_client
from app.services.events import emit as emit_event
from app.services.vision_rules import execute_rule, load_rules, rule_for

log = get_logger("vision_ingest")


class VisionIngest:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_seq = 0
        self.ingested = 0
        self.connected = False
        self.last_error = ""
        self.last_ingest_at = 0.0
        self._backfilled = False
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="vision-ingest",
                                        daemon=True)
        self._thread.start()
        log.info("视觉事件摄入线程已启动 (base=%s)", camera_client.base_url())

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None

    def status(self) -> dict:
        cfg = get_config().vision
        return {
            "base_url": camera_client.base_url(),
            "connected": self.connected,
            "error": self.last_error,
            "last_seq": self.last_seq,
            "ingested": self.ingested,
            "last_ingest_at": self.last_ingest_at,
            "enabled": bool(cfg.get("enabled", True)),
            "auto_execute": bool(cfg.get("auto_execute", False)),
            "poll_ms": int(cfg.get("poll_ms") or 800),
        }

    # ---------- 主循环 ----------
    def _loop(self) -> None:
        while not self._stop.is_set():
            cfg = get_config().vision
            try:
                poll = max(200, int(cfg.get("poll_ms") or 800)) / 1000.0
            except Exception:
                poll = 0.8
            try:
                if not bool(cfg.get("enabled", True)):
                    self.connected = False
                    self.last_error = ""
                    self._stop.wait(1.5)
                    continue
                self._tick()
            except Exception as e:                      # noqa: BLE001
                self.last_error = str(e)[:160]
                log.debug("视觉摄入异常: %s", e)
            self._stop.wait(poll)

    def _tick(self) -> None:
        if not self._backfilled:
            # 后端重启后先把相机服务内存里的最近记录补齐（幂等，重复的会被 seq 挡住）
            st, j, err = camera_client.get_json("/vision/records?limit=50")
            if st != 200 or err:
                self.connected = False
                self.last_error = err or "相机服务未就绪"
                return
            self.connected = True
            self.last_error = ""
            recs = (j or {}).get("records") or []
            for ev in sorted(recs, key=lambda x: int(x.get("seq") or 0)):
                self._ingest(ev)
                self.last_seq = max(self.last_seq, int(ev.get("seq") or 0))
            self._backfilled = True
            return

        st, j, err = camera_client.get_json("/vision/last?since=%d" % self.last_seq)
        if st != 200 or err:
            self.connected = False
            self.last_error = err or "相机服务未就绪"
            return
        self.connected = True
        self.last_error = ""
        ev = (j or {}).get("event")
        if ev:
            # ★ 先落库再推进 seq：落库失败时下一轮会重试，不会丢事件
            self._ingest(ev)
        self.last_seq = max(self.last_seq, int((j or {}).get("seq") or 0))

    # ---------- 单条摄入 ----------
    def _ingest(self, ev: dict) -> None:
        if not isinstance(ev, dict):
            return
        color = str(ev.get("color") or "")
        rec = dict(ev)
        rec["source"] = "camera"
        rec["outcome"] = "recorded"

        rule = rule_for(color)
        if rule is not None:
            rec["program_id"] = rule.get("program_id")
            rec["program_name"] = rule.get("program_name") or ""
            rres = execute_rule(rule, color=color, seq=int(ev.get("seq") or 0))
            rec["outcome"] = rres.get("outcome") or "recorded"
            rec["note"] = rres.get("note") or ""
        else:
            rec["note"] = "无匹配规则"

        db = SessionLocal()
        try:
            row = insert_vision_record(db, rec)
        finally:
            db.close()

        # 事件总线：审计 + WS 广播（前端语音播报就靠这条）
        level = "info"
        if rec["outcome"] in ("error", "blocked"):
            level = "warn"
        emit_event(
            "vision", level, "vision.detect",
            "视觉分拣：%s" % (color or "未知"),
            {
                "seq": ev.get("seq"), "color": color, "hex": ev.get("hex"),
                "conf": ev.get("conf"), "de": ev.get("de"), "alt": ev.get("alt"),
                "ratio": ev.get("ratio"), "area": ev.get("area"),
                "reason": ev.get("reason"),
                "outcome": rec["outcome"], "note": rec["note"],
                "program_id": rec.get("program_id"),
                "program_name": rec.get("program_name"),
                "images": ev.get("images") or {},
                # 只有新入库的才推进计数（重复的不算）
                "duplicate": row is None,
            },
            actor="vision",
        )
        if row is not None:
            self.ingested += 1
            self.last_ingest_at = time.time()

    def prune(self, days: int) -> int:
        db = SessionLocal()
        try:
            return prune_vision_records(db, days)
        finally:
            db.close()


ingest = VisionIngest()
