# -*- coding: utf-8 -*-
"""
示教器运行模式（L1）。

## 两个来源，控制器实测优先

档位（T1 / T2 / AUTO / REMOTE）有两个来源，优先级不同：

1. **控制器实测**（优先）：控制器的状态字里带 manual / auto / remote 位
   （`services/modbus.py` 的 `STATUS_BITS` 解码 → `rc_snapshot()["mode"]`）。
   采集循环每 4s 读一次并喂给 `runmode.observe()`。旋钮实际打在哪一档，
   控制器自己最清楚 —— **实测到 auto/remote 就直接算"已确认"**，不需要人工声明。
   这也是 `rc_ready.py` 的「一键就绪」一直在用的判据（手动档直接拒绝就绪）。
   ★ 实测值有 30s 有效期（OBSERVE_TTL_SEC）：控制器掉线/断电后灯自己回"未确认"，
     不会拿着半小时前的绿色继续骗人。
   ★ 只有 3 个 bit，**分不出 T1 与 T2** —— 实测 manual 统一按 T1 处理（两档对 PC
     的后果相同：都忽略上位机指令），文案里如实写"手动档（T1/T2）"。

2. **人工声明**（回落）：控制器读不到时（模拟 / 掉线 / 未上电）才回落到这里。
   操作员在底栏「示教器」灯里按旋钮实际位置声明。声明值**不持久化**：
   后端重启后回到"未确认"。理由很直接 —— 重启和旋钮位置无关，拿着旧声明
   放行等于凭空假设旋钮没被人拨过。宁可多问一次，也不要误放行。

## ★ 档位与 PC 控制的真实关系（2026-09-23 实机确认，与官方手册语义相反）

| 模式 | 含义 | PC 点动/下发 |
|---|---|---|
| T1 | 手动低速（示教） | **不可用** —— 控制器忽略上位机指令 |
| T2 | 手动高速 | **不可用** —— 同上 |
| AUTO | 自动运行 | **可用** —— 上位机控制的唯一有效档位（实测） |
| REMOTE | 远程 / 上位机控制 | 可用（与 AUTO 等效） |

★ 为什么与"官方语义"相反：官方手册说"AUTO 下拒绝手动点动"指的是**示教器按键**；
  而 PC 走的是"常驻点动程序 200/JOGSVC + MJOINT"通道 —— 它本质是**自动运行**，
  控制器不在 AUTO/远程档时根本不理会外部指令（T1/T2 下写目标角、触发位全都无效）。
  实测：AUTO 档下点动链路完整可用；T1/T2 下寄存器写成功但机器人不动。

★ 本模块**只做判定与留痕**，不写任何寄存器。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger

log = get_logger("runmode")

MODES: Tuple[str, ...] = ("T1", "T2", "AUTO", "REMOTE")

LABEL: Dict[str, str] = {
    "T1": "T1 手动低速",
    "T2": "T2 手动高速",
    "AUTO": "AUTO 自动",
    "REMOTE": "远程控制",
}

DESC: Dict[str, str] = {
    "T1": "手动示教档：控制器忽略上位机指令，PC 点动/下发不可用（请在示教器上直接操作）",
    "T2": "手动高速档：控制器忽略上位机指令，PC 点动/下发不可用",
    "AUTO": "自动档：★ 实测确认 —— 上位机点动/下发唯一有效档位",
    "REMOTE": "远程档：上位机控制可用（与 AUTO 等效）",
}

# 该档位下是否允许 PC 点动（2026-09-23 实机确认：只有 AUTO/REMOTE 有效）
JOGGABLE: Dict[str, bool] = {"T1": False, "T2": False, "AUTO": True, "REMOTE": True}

# 声明有效期：超时视为"未确认"。★ 不是安全计时器，只是防止"昨天的声明"给今天授权。
CLAIM_TTL_SEC = 2 * 3600

# 控制器实测值的有效期：采集循环每 4s 喂一次，30s ≈ 连丢 7 拍才失效。
# ★ 与 CLAIM_TTL_SEC 差三个数量级是刻意的：人工声明是"一句话的授权"，防的是昨天；
#   实测是"旋钮此刻在哪"，防的是控制器掉线/断电后界面还挂着绿色。
OBSERVE_TTL_SEC = 30.0

# 控制器状态字 mode → 本模块档位 id。
# ★ 只有三个 bit（manual/auto/remote），**控制器分不出 T1 与 T2** —— 两者对 PC 的
#   后果完全一样（都忽略上位机指令，见 JOGGABLE），所以统一映射到 T1 并在
#   label/desc 里如实说明"手动档 T1/T2 无法区分"，不假装知道是 T1 还是 T2。
OBSERVED_MAP: Dict[str, str] = {"auto": "AUTO", "remote": "REMOTE", "manual": "T1"}

# 默认：声明为 T1/T2（控制器忽略 PC 指令的档位）时拒绝点动。
# 可在 robot.yaml 关掉（motion.jog.require_manual_mode=false）
_REQUIRE_MANUAL_DEFAULT = True


def _cfg_bool(key: str, default: bool) -> bool:
    try:
        from app.core.config import get_config
        v = get_config().get("motion", "jog", key, default=default)
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "no", "off", "")
        return bool(v)
    except Exception:
        return default


class RunMode:
    """示教器模式声明的唯一持有者（线程安全）。

    两个数据源合并成一份对外状态（见模块头：控制器实测优先）：
      `observe()` ← 采集循环/`/rc-status` 喂进来的控制器实测档位
      `claim()`   ← 操作员在底栏手动声明（控制器读不到时的回落）
    对外只暴露一个 `state()`，调用方**不需要**知道档位是从哪来的。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = ""          # 已声明的档位；空字符串表示从未声明
        self._at = 0.0           # 声明时间（epoch 秒）
        self._actor = ""
        self._note = ""
        self._observed = ""      # 控制器实测档位（已映射为 MODES 里的 id）
        self._observed_raw = ""  # 控制器原始 mode 字（auto / remote / manual）
        self._observed_at = 0.0  # 最近一次实测时间（epoch 秒）

    # ---------- 写 ----------
    def observe(self, mode: str) -> dict:
        """喂入控制器实测档位（`rc_snapshot()["mode"]`）。返回本次是否有变化。

        ★ 只在**读到了明确档位**时更新：`unknown`（三个模式位全 0，典型是控制器
          刚上电/正在切换的瞬间）视为"这一拍没读到"，保留上一次实测值让它按
          OBSERVE_TTL_SEC 自然过期 —— 若见 unknown 就清空，界面会在上电瞬间
          黄一下再变绿，比"最多 30s 的旧值"更误导人。
        """
        m = str(mode or "").strip().lower()
        mapped = OBSERVED_MAP.get(m, "")
        if not mapped:
            return {"changed": False, "mode": "", "previous": ""}
        with self._lock:
            prev = self._observed
            self._observed = mapped
            self._observed_raw = m
            self._observed_at = time.time()
            return {"changed": prev != mapped, "mode": mapped, "previous": prev}

    def claim(self, mode: str, actor: str = "", note: str = "") -> dict:
        m = str(mode or "").strip().upper()
        if m not in MODES:
            raise ValueError("模式只能是 " + " / ".join(MODES) + " 之一（收到 " + (m or "空") + "）")
        with self._lock:
            self._mode = m
            self._at = time.time()
            self._actor = str(actor or "")[:32]
            self._note = str(note or "")[:120]
            return self._state_locked()

    def clear(self) -> dict:
        """撤销**人工声明**。★ 不动控制器实测值 —— 实测来自硬件，不是"声明"，
        清掉它等于假装读不到旋钮位置（控制器还连着的话下一拍又会喂回来）。"""
        with self._lock:
            self._mode = ""
            self._at = 0.0
            self._actor = ""
            self._note = ""
            return self._state_locked()

    def reset(self) -> None:
        """全量复位（声明 **和** 实测值）。

        ★ 只给测试与排障用：实测值由硬件驱动，业务上不存在"清一下"这个动作 ——
          所以不并进 clear()，免得哪天有人在业务代码里调了它、把真实档位抹掉。
        """
        with self._lock:
            self._mode = ""
            self._at = 0.0
            self._actor = ""
            self._note = ""
            self._observed = ""
            self._observed_raw = ""
            self._observed_at = 0.0

    # ---------- 读 ----------
    def _state_locked(self) -> dict:
        now = time.time()
        m, at = self._mode, self._at
        age = (now - at) if at else None
        fresh = bool(m) and age is not None and age <= CLAIM_TTL_SEC
        claim_mode = m if fresh else ""

        # ★ o_id 是**映射后**的档位 id（AUTO/REMOTE/T1），o_word 才是控制器原始字
        #   （auto/remote/manual）—— 别把这两个混用：字段 observed_raw 的语义是"原始字"，
        #   界面/排障要靠它区分"实测到的到底是哪一位"。
        o_id, o_word, o_at = self._observed, self._observed_raw, self._observed_at
        o_age = (now - o_at) if o_at else None
        o_fresh = bool(o_id) and o_age is not None and o_age <= OBSERVE_TTL_SEC
        observed_mode = o_id if o_fresh else ""

        # ★ 实测优先：旋钮实际打在哪一档，控制器自己最清楚（人工声明只在读不到时兜底）
        if observed_mode:
            mode, source = observed_mode, "controller"
        elif claim_mode:
            mode, source = claim_mode, "manual"
        else:
            mode, source = "", ""

        confirmed = bool(mode)
        # 控制器只报"手动档"，分不出 T1/T2 —— 文案如实说明，不假装知道是哪一档
        manual_gear = source == "controller" and self._observed_raw == "manual"
        label = ("手动档（T1/T2）" if manual_gear else LABEL.get(mode)) or "未确认"
        if manual_gear:
            desc = ("控制器实测为**手动档**（状态字分不出 T1 / T2）：两档下控制器都忽略"
                    "上位机指令，PC 点动/下发不可用；请把模式开关拨到 AUTO 或 远程")
        else:
            desc = DESC.get(mode) or "读不到示教器档位（控制器未上电/未连接）；手动点动会被登记为『模式未声明』"
        if source == "controller" and not manual_gear:
            desc = "控制器实测档位，已自动确认：" + desc
        return {
            "mode": mode,                      # ★ 已生效档位；过期/未声明的都是 ""
            "confirmed": confirmed,
            "source": source,                  # controller（实测）/ manual（声明）/ ""（未确认）
            "label": label,
            "desc": desc,
            "joggable": confirmed and bool(JOGGABLE.get(mode, True)),
            "claimed_mode": m,                 # 原始声明值（可能已过期），仅用于界面提示
            "stale": bool(m) and not fresh,
            "actor": self._actor if fresh else "",
            "note": self._note if fresh else "",
            "claimed_at": (datetime.fromtimestamp(at, timezone.utc).isoformat() + "Z") if at else "",
            "age_sec": int(age) if age is not None else None,
            "ttl_sec": CLAIM_TTL_SEC,
            "observed_mode": observed_mode,     # 实测生效值（过期即空）
            "observed_raw": o_word if o_fresh else "",
            "observed_age_sec": int(o_age) if (o_fresh and o_age is not None) else None,
            "observe_ttl_sec": OBSERVE_TTL_SEC,
            "options": [
                {"id": k, "label": LABEL[k], "desc": DESC[k], "joggable": JOGGABLE[k]}
                for k in MODES
            ],
        }

    def state(self) -> dict:
        with self._lock:
            return self._state_locked()

    # ---------- 判定 ----------
    def level(self) -> str:
        """给状态灯用的等级：ok（已确认且允许 PC 控制）/ warn（未确认）/ err（手动档）。

        ★ 控制器实测到 AUTO/REMOTE 时这里直接是 ok —— 底栏绿灯不需要人工声明，
          这正是"示教器上面 auto 模式就已确认"的落点。
        """
        st = self.state()
        if not st["confirmed"]:
            return "warn"
        return "ok" if st["joggable"] else "err"

    def check_jog(self) -> Tuple[bool, str, dict]:
        """点动前置判定 → (是否允许, 拒绝原因, 状态快照)。

        ★ 只拦"确定无效"这一种情形 —— 实机确认手动档（T1/T2）下控制器
          **忽略一切上位机指令**（寄存器写得进去、机器人纹丝不动），与其让用户
          对着"写成功但没动"的假象排查半天，不如直接说清楚。档位来源可能是
          控制器实测，也可能是人工声明，两者都算数。
          未确认时**放行**（否则真机联调前完全没法用），但返回的 state 里
          confirmed=False，调用方应记一条『模式未声明』事件 —— 与围栏互锁
          "未上报则放行但留痕"的策略保持一致。
        """
        st = self.state()
        if st["confirmed"] and not st["joggable"] and _cfg_bool("require_manual_mode", _REQUIRE_MANUAL_DEFAULT):
            why = ("控制器实测为手动档" if st["source"] == "controller" else "示教器已声明为")
            return False, (
                "示教器" + why + " " + str(st["label"]) + "，该档位下控制器忽略上位机指令；"
                "请把模式开关拨到 AUTO 或 远程"
            ), st
        return True, "", st

    def summary(self) -> dict:
        """给自检/状态条用的精简摘要（不含 options，省带宽）。"""
        st = self.state()
        return {
            "mode": st["mode"],
            "confirmed": st["confirmed"],
            "source": st["source"],
            "stale": st["stale"],
            "label": st["label"],
            "desc": st["desc"],
            "joggable": st["joggable"],
            "level": self.level(),
            "actor": st["actor"],
            "age_sec": st["age_sec"],
            "observed_mode": st["observed_mode"],
            "observed_raw": st["observed_raw"],
            "observed_age_sec": st["observed_age_sec"],
        }


runmode = RunMode()
