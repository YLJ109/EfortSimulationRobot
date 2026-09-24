// =====================================================================
// 底栏状态灯：**状态 → 外观** 的唯一映射（纯函数，无 Vue、无 DOM）。
//
// ★ 为什么单独抽一个文件：这类"按状态决定颜色/文案"的代码写错了
//   **在界面上看着仍然正常**（缺字段会静默走兜底分支，甚至回退成绿色），
//   只有 headless 断言能把每一档的最终结果钉死。
//   tools/verify_link.mjs 就是对着本文件逐状态断言的，改文案必须同步改断言。
//
// 数据来源：GET /api/system/guide 的 links 段（后端 api/system.py 的 _links()）。
// ★ links 是"现在是什么"，任何情况下三盏灯都必须齐全 —— 这是硬要求：
//   现场靠它判断"到底是网线没插、相机没开，还是示教器档位不对"。
// =====================================================================

/** 色调：ok=绿(正常) warn=黄(待确认/部署态) err=红(故障) off=灰(拿不到状态)。 */
export const TONES = ["ok", "warn", "err", "off"];

/** 示教器四档。★ id 必须与后端 backend/app/services/runmode.py 的 MODES 完全一致，
 *  verify_link.mjs 会直接读那个 python 文件做漂移守卫。
 *  ★ desc 必须与后端 runmode.py 的 DESC 同义（2026-09-23 实机确认后反转：
 *    上位机控制的唯一有效档位是 AUTO/远程；T1/T2 下控制器忽略上位机指令）。 */
export const RUN_MODES = [
  { id: "T1", label: "T1 手动低速", desc: "手动示教档：控制器忽略上位机指令，PC 点动/下发不可用" },
  { id: "T2", label: "T2 手动高速", desc: "手动高速档：控制器忽略上位机指令，PC 点动/下发不可用" },
  { id: "AUTO", label: "AUTO 自动", desc: "自动档：★ 实测确认 —— 上位机点动/下发唯一可用档位" },
  { id: "REMOTE", label: "远程控制", desc: "远程档：上位机控制可用（与 AUTO 等效）" },
];

/**
 * 后端 level → 色调。
 * ★ 未知 level 一律回 off（灰），**不能**兜底成 ok：兜底成绿色会让
 *   "后端加了个新等级但前端没适配"这种问题彻底看不见。
 */
export function toneOf(level) {
  if (level === "ok") return "ok";
  if (level === "warn") return "warn";
  if (level === "err" || level === "error") return "err";
  return "off";
}

function missing(key, label, icon, hint) {
  return {
    key, icon, label, value: "未知", detail: hint, tone: "off",
    title: label + "：" + hint,
    available: false,
  };
}

/** 机器人链路灯。 */
export function robotPill(links) {
  const r = links && links.robot;
  if (!r || !r.state) {
    return missing("robot", "机器人", "plug", "未获取到链路状态（后端自检不可用）");
  }
  const addr = r.host ? r.host + (r.port ? ":" + r.port : "") : "";
  const extra = r.simulate ? " · simulate=" + r.simulate : "";
  return {
    key: "robot", icon: "plug", label: "机器人",
    value: r.label || "未知",
    detail: r.detail || "",
    tone: toneOf(r.level),
    available: true,
    // 详情面板用
    state: r.state, host: r.host || "", port: r.port || "",
    connected: !!r.connected, simulated: !!r.simulated, simulate: r.simulate || "",
    title: "机器人链路 · " + (r.label || "未知") + (addr ? "（" + addr + "）" : "") + extra +
      (r.detail ? "\n" + r.detail : ""),
  };
}

/** 摄像头灯。 */
export function cameraPill(links) {
  const c = links && links.camera;
  if (!c || !c.state) {
    return missing("camera", "摄像头", "camera", "未获取到相机状态（后端自检不可用）");
  }
  const bits = [];
  if (c.device) bits.push(c.device);
  if (c.ip) bits.push(c.ip);
  if (c.resolution && c.resolution !== "-") bits.push(c.resolution);
  if (c.fps) bits.push(c.fps + " fps");
  return {
    key: "camera", icon: "camera", label: "摄像头",
    value: c.label || "未知",
    detail: c.ok && bits.length ? bits.join(" · ") : (c.detail || ""),
    tone: toneOf(c.level),
    available: true,
    state: c.state, base: c.base || "", port: c.port || "",
    opened: !!c.opened, device: c.device || "", ip: c.ip || "",
    resolution: c.resolution || "-", fps: c.fps || 0,
    error: c.error || "",
    title: "摄像头 · " + (c.label || "未知") +
      (c.base ? "（" + c.base + "）" : "") + (c.detail ? "\n" + c.detail : ""),
  };
}

/**
 * 示教器档位灯。
 * ★ 未确认时也要亮灯（warn）—— 你要的就是"不管有没有 AUTO 都要显示"。
 *   档位有**两个来源**（后端 runmode.py：控制器实测优先）：
 *     source="controller" → 控制器状态字里读到的档位，**自动确认**，不需要人工声明；
 *     source="manual"     → 控制器读不到，操作员按旋钮实际位置声明的；
 *     source=""           → 都没有 → "未确认"（如实呈现，不假装知道）。
 *   ★ 界面必须把来源写出来：现场看到"已确认"时要知道这是**硬件读到的**还是
 *     **人说的** —— 前者可信度完全不同，混在一起显示等于把人工声明的可信度
 *     抬到了实测的水平。
 */
export function modePill(links) {
  const m = links && links.run_mode;
  if (!m) {
    return {
      ...missing("run_mode", "示教器", "terminal", "后端未返回档位信息"),
      confirmed: false, mode: "", source: "", joggable: false, options: RUN_MODES,
    };
  }
  const confirmed = !!m.confirmed;
  const source = m.source || "";
  const value = m.label || "未确认";
  const srcText = { controller: "控制器实测", manual: "手动声明" }[source] || "";
  return {
    key: "run_mode", icon: "terminal", label: "示教器",
    value,
    detail: m.desc || "",
    tone: toneOf(m.level),
    available: true,
    mode: m.mode || "",
    claimedMode: m.claimed_mode || m.mode || "",
    confirmed,
    source,
    sourceText: srcText,
    observedMode: m.observed_mode || "",
    observedAgeSec: typeof m.observed_age_sec === "number" ? m.observed_age_sec : null,
    stale: !!m.stale,
    joggable: !!m.joggable,
    actor: m.actor || "",
    ageSec: typeof m.age_sec === "number" ? m.age_sec : null,
    options: RUN_MODES,
    title: "示教器档位 · " + value + (srcText ? "（" + srcText + "）" : "") +
      (m.desc ? "\n" + m.desc : "") +
      (confirmed ? "" : "\n控制器读不到档位（未上电/未连接），点这里按旋钮实际位置声明"),
  };
}

/** 三盏灯（顺序即显示顺序：按"会不会伤人"排，围栏不在其中，它单独一片）。 */
export function allPills(links) {
  return [robotPill(links), cameraPill(links), modePill(links)];
}

const RANK = { off: 0, ok: 1, warn: 2, err: 3 };

/** 三盏灯取最差色调，供底栏做整体提示（err > warn > ok > off）。 */
export function stripTone(pills) {
  let worst = "off";
  for (const p of (pills || [])) {
    if (RANK[p.tone] > RANK[worst]) worst = p.tone;
  }
  return worst;
}

/** 三盏灯里第一颗非 ok 的（用于"一句话说明现在到底怎么了"）。 */
export function worstPill(pills) {
  let out = null;
  for (const p of (pills || [])) {
    if (!out || RANK[p.tone] > RANK[out.tone]) out = p;
  }
  return out && out.tone !== "ok" ? out : null;
}

/** 档位声明 / 请求控制 是否可用（未拿到令牌时只读）。 */
export function canClaim(hasToken, pill) {
  return !!(hasToken && pill && pill.key === "run_mode");
}
