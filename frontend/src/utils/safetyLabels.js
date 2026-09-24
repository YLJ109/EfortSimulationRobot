// =====================================================================
// 安全围栏的**展示文案**纯函数（无 Vue、无 DOM）。
//
// ★ 为什么单独成模块：
//   「安全围栏报警条」（#safety-banner，顶部，只在危险/碰撞时出现）和
//   「安全围栏状态片」（#safety-chip，3D 视口右下角常驻）读的是**同一份**
//   评估结果，却各自渲染不同粒度的话术。以前两组文案都写在 App.vue 里，
//   状态片挪进视口后如果照抄一份，改一处必漏一处（"危险"与"接近"的阈值
//   表述会因为两处不一致而自相矛盾）。
//   抽成纯函数后，两边共用，且能在 node 里逐状态断言（tools/verify_safety_text.mjs）。
//
// 输入 s 的形状（由 three/safety.js 每帧评估、经 manager.onSafety 广播）：
//   { state: "safe"|"warn"|"danger"|"hit", ratio, clearance(米), zoneId, zoneName,
//     source: "robot"|"ghost" }
//   ★ zoneId === "ground" 表示这是**地面碰撞**而不是围栏余量，话术完全不同。
//   ★ source === "ghost" 表示量的是**残影预演的目标位姿**（"要去的地方会不会撞"），
//     而不是实体机的当前位姿 —— 判据同源见 three/manager.js 的 evalSafety()。
// =====================================================================

/** 状态 → 两字标签。 */
export const STATE_LABEL = {
  safe: "安全", warn: "接近", danger: "危险", hit: "碰撞",
};

/** 是否走"地面碰撞"话术（zoneId 为 ground 时，量的是离台面高度）。 */
export function isGroundState(s) {
  return !!s && s.zoneId === "ground";
}

/** 报警条正文（长句，只在 warn 及以上出现）。 */
export function safetyText(s) {
  const st = (s && s.state) || "safe";
  if (isGroundState(s)) {
    if (st === "hit") return "碰撞报警！机器人已触及台面";
    if (st === "danger") return "危险：机器人即将触及台面";
    if (st === "warn") return "注意：机器人接近台面（离台面高度偏低）";
    return "安全：机器人离台面高度正常";
  }
  if (st === "hit") return "碰撞报警！机器人已触及安全围栏";
  if (st === "danger") return "危险：即将碰撞安全围栏（余量 < 10%）";
  if (st === "warn") return "注意：接近安全围栏（余量 < 30%）";
  // ★ 原来这里直接 return 上面那句 warn 文案 —— "安全"状态会输出"注意：接近…"。
  //   顶部报警条在 safe 时不显示（showBanner 要求 >= warn），所以现场看不出来；
  //   但 safetyText 已经是两处共用的公共函数，语义必须自洽。补一句专属文案。
  return "安全：距围栏内表面余量正常";
}

/** 报警条副文 / 状态片数值：把 clearance(米) 换成 mm。 */
export function safetySub(s) {
  const c = (s && s.clearance) || 0;
  if (isGroundState(s)) {
    return c > 0 ? `最低点离台面 ${(c * 1000).toFixed(0)} mm` : "已触台面，请立即停止";
  }
  const zone = s && s.zoneName ? ` · ${s.zoneName}` : "";
  return (c > 0 ? `剩约 ${(c * 1000).toFixed(0)} mm` : "已越界，请立即停止") + zone;
}

/** 状态片两字标签；未知状态一律回退"安全"（绝不回退成空白）。 */
export function safetyLabel(s) {
  const st = (s && s.state) || "safe";
  return STATE_LABEL[st] || "安全";
}

/** 状态片作用域前缀：地面碰撞 or 围栏余量。 */
export function safetyScope(s) {
  return isGroundState(s) ? "地面" : "安全围栏";
}

/**
 * 悬停提示。
 * ★ 带上**评估对象**：同一份评估结果有时量的是残影预演的目标位姿（"要去的地方"），
 *   有时量的是实体机的当前位姿（"现在在哪"）。两者数值含义完全不同，
 *   不写清楚就会出现"机器人明明还在安全区，状态片却报危险"的困惑。
 * ★ 只在**尾部追加**括号说明，不改前缀 —— 状态片/报警条的作用域二分
 *   （"地面" vs "安全围栏"）必须与 isGroundState 严格同源，见 verify_safety_text.mjs。
 */
export function chipTitle(s) {
  const base = isGroundState(s)
    ? "地面碰撞：机器人最低点离台面的高度"
    : "安全围栏：距围栏内表面的最小余量";
  return (s && s.source === "ghost") ? base + "（按残影预演的目标位姿）" : base;
}
