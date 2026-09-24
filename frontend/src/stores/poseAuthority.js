// =====================================================================
// 姿态权威判据（纯函数，阶段 7 重构）
//
// 为什么单独抽一个文件：displayQ / displayTcp / poseSource 三者的优先级
// 是**全站唯一的姿态判据**，一旦写错，"3D 画面"与"读数卡"会一起错。
// 放在 Pinia getter 里没法在 node 里直接测（要先搭 store 实例），
// 抽成纯函数后 tools/verify_pose_authority.mjs 可以把每个分支钉死：
//   override(前台/后台) → demo → 指令(模拟未走位) → 遥测。
//
// 入参 s 是 robot store 的 state 子集：
//   { override: {owner,q,tcp}|null, demo:{on,q,tcp},
//     telemetry:{q,tcp,simulated,cmd,cmdTcp,tracking}, activeView }
// 返回 { q, tcp, source }。
// =====================================================================

/** 姿态来源解析 —— 全站唯一实现，任何视图不得再自己写一遍优先级。 */
export function resolvePose(s) {
  // ① 本地沙盘覆盖（模拟仿真）：只在 owner === activeView 时生效，
  //    切页后残留的 override 不会污染别的页面。
  if (s.override && s.override.owner === s.activeView) {
    return {
      q: s.override.q,
      tcp: s.override.tcp || s.telemetry.tcp,
      source: "override",
    };
  }
  // ② 未连接占位：唯一被允许接管显示姿态的特例（只有 RealMonitor 能设；未连接 → 全 0）。
  if (s.demo && s.demo.on) {
    return { q: s.demo.q, tcp: s.demo.tcp, source: "demo" };
  }
  // ③ 指令目标：模拟模式下后端若尚未支持仿真走位（tracking 恒 false），
  //    它推来的姿态是与指令无关的缓慢扫掠 —— 那种帧不能当"机器人现在在哪"。
  //    有 cmd 时显示 cmd；Stage C 让仿真机真的走向目标后（tracking=true）
  //    本分支自动让位给遥测。
  if (s.telemetry.simulated && !s.telemetry.tracking && s.telemetry.cmd) {
    return {
      q: s.telemetry.cmd,
      tcp: s.telemetry.cmdTcp || s.telemetry.tcp,
      source: "command",
    };
  }
  // ④ 遥测真值：真机读回 / 仿真机体位。**永远不被任何视图抑制**。
  return { q: s.telemetry.q, tcp: s.telemetry.tcp, source: "telemetry" };
}
