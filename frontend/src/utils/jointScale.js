// =====================================================================
// 关节读数条的量程归一化（纯函数，无 Vue / 无 DOM）。
//
// ★ 为什么单独成模块：
//   这几行数学以前写在 RealMonitor 里，只读展示用，看着"不可能错"——
//   但它的边界全在**异常输入**上：限位缺失、span 为 0、读数是 NaN、
//   角度越过限位（真机在标定前就有可能超一点）。
//   一旦算错，条宽会溢出容器或变成 NaN%，而页面**看起来"只是条不太对"**，
//   没人会去查。所以判据抽出来，由 tools/verify_robot_params.mjs 逐种边界钉死。
//
// 约定（写清楚，避免调用方各自猜）：
//   · 限位不合法（不是长度 6 的 {min,max}）→ sanitizeLimits 返回 null，
//     由调用方决定兜底（组件用 store 的 DEFAULT_LIMITS），本模块**不擅自造**限位。
//   · 读数不是有限数（undefined / null / NaN / 字符串）→ 按 0 处理，再钳位。
//     ★ 不返回 NaN：NaN 会一路传到 CSS width 变成 "NaN%"，条直接消失。
//   · 读数越界 → 钳到 [min, max]，条停在两端，绝不溢出。
//   · 返回值恒在 [0, 100]。
// =====================================================================

/** 角度读数条的合法限位（长度 6、每项 min/max 都是有限数且 min < max）。 */
export function sanitizeLimits(raw) {
  if (!Array.isArray(raw) || raw.length !== 6) return null;
  const out = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") return null;
    const min = Number(item.min);
    const max = Number(item.max);
    if (!Number.isFinite(min) || !Number.isFinite(max) || !(min < max)) return null;
    out.push({ name: item.name, min, max });
  }
  return out;
}

/**
 * 单轴读数的条宽百分比（0~100）。
 * @param {number} value 当前角度（度）
 * @param {{min:number,max:number}} lim 该轴限位
 */
export function barPct(value, lim) {
  if (!lim) return 0;
  const span = Number(lim.max) - Number(lim.min);
  if (!Number.isFinite(span) || span <= 0) return 0;
  const v0 = Number(value);
  const v = Number.isFinite(v0) ? v0 : 0;          // 非有限数按 0 处理，绝不外传 NaN
  const clamped = Math.min(Number(lim.max), Math.max(Number(lim.min), v));
  const pct = ((clamped - Number(lim.min)) / span) * 100;
  return Math.min(100, Math.max(0, pct));
}

/** 便于模板直接拿到 CSS 宽度。 */
export function barWidthStyle(value, lim) {
  return { width: barPct(value, lim).toFixed(1) + "%" };
}
