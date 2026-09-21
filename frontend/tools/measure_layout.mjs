// =====================================================================
// 场景布局体检（headless）：一次打印"房间 / 站台 / 机器人 / 报警垫 / 灯带"
// 的关键几何尺寸与相互关系，用于改动尺寸后用**数字**核对比例是否还合理
// （浏览器里看不出"台面高了 2cm""机器人悬空 3cm"这种问题）。
//
// 运行： node tools/measure_layout.mjs
// 附带： node tools/measure_layout.mjs --json   → 只输出 JSON，便于脚本消费
// =====================================================================
import * as THREE from "three";
import { buildRobot } from "../src/robot/robotModel.js";
import { FALLBACK_DH } from "../src/config.js";
import { buildCell, PLATFORM_H, PLATFORM_SIZE, PLATFORM_TOP_SIZE, FLOOR_SIZE } from "../src/three/cell.js";
import { buildLab, LAB_SIZE, LAB_HEIGHT } from "../src/three/lab.js";
import { SafetyFence } from "../src/three/safety.js";

// cell.js 的程序化贴图需要 canvas：headless 下打桩（只影响贴图像素，不影响几何）
if (typeof globalThis.document === "undefined") {
  const grad = { addColorStop() {} };
  const ctx = new Proxy({}, {
    get(_t, k) { return (k === "createRadialGradient" || k === "createLinearGradient") ? () => grad : () => {}; },
    set() { return true; },
  });
  globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => ctx }) };
}

const r3 = (v) => Math.round(v * 1000) / 1000;
const out = {};
const bbox = (o) => {
  const b = new THREE.Box3().setFromObject(o);
  return { min: [r3(b.min.x), r3(b.min.y), r3(b.min.z)],
           max: [r3(b.max.x), r3(b.max.y), r3(b.max.z)] };
};
const bboxOf = (root, name) => {
  const o = root.getObjectByName(name);
  return o ? bbox(o) : null;
};

// ---- 房间 ----
const lab = buildLab();
lab.group.updateMatrixWorld(true);
const labB = bbox(lab.group);
out.room = {
  size_m: LAB_SIZE, height_m: LAB_HEIGHT,
  y: [labB.min[1], labB.max[1]],
  lightStrips: (() => {
    let n = 0, y = null;
    lab.group.traverse((o) => {
      if (o.name !== "lab-lightstrip") return;
      n++; y = r3(new THREE.Box3().setFromObject(o).min.y);
    });
    return { count: n, atY: y };
  })(),
};

// ---- 工作区 + 站台 ----
const cell = buildCell();
cell.group.updateMatrixWorld(true);
const plat = bboxOf(cell.group, "platform-body");
const platTop = bboxOf(cell.group, "platform-top");
const shadow = bboxOf(cell.group, "floor-shadow");
out.workFloor = { size_m: FLOOR_SIZE };
out.platform = {
  size_m: PLATFORM_SIZE, height_m: PLATFORM_H, topSize_m: PLATFORM_TOP_SIZE,
  surfaceY: platTop ? platTop.max[1] : null,          // ★ 机器人立足面
  shadowY: shadow ? shadow.min[1] : null,
  feet: (() => { let n = 0; cell.group.traverse((o) => { if (o.name === "platform-foot") n++; }); return n; })(),
  // 分层：调平脚 → 踢脚底盘 → 台身 → 台面檐口 → 拉丝台面
  layers: ["platform-foot", "platform-plinth", "platform-body",
           "platform-band", "platform-toprim", "platform-top"]
    .map((n) => {
      const b = bboxOf(cell.group, n);
      return b ? { name: n, y: [b.min[1], b.max[1]], span_x: r3(b.max[0] - b.min[0]) } : null;
    })
    .filter(Boolean),
};

// ---- 机器人（抬到台面上，与 manager 一致） ----
const { root } = buildRobot(FALLBACK_DH, "floor");
root.position.y = PLATFORM_H;
const sc = new THREE.Scene(); sc.add(root); sc.updateMatrixWorld(true);
const rb = bbox(root);
out.robot = {
  bbox: rb, height_m: r3(rb.max[1] - rb.min[1]),
  sitsOnPlatform: r3(rb.min[1] - PLATFORM_H),   // ≈0 表示正好坐在台面上
  topOverPlatform_m: r3(rb.max[1] - PLATFORM_H),
  headroomToCeiling_m: r3(labB.max[1] - rb.max[1]),
};

// ---- 地面报警垫（manager 会把它铺到台面上） ----
const cfg = {
  version: 1, enabled: true,
  zones: [{
    id: "z1", name: "主工作区", enabled: true, shape: "rect",
    center: { x: 0, z: 0 }, half: { x: 0.82, z: 0.82 }, radius: 0.9,
    height: 1.2, walls: true, posts: { enabled: true, size: 0.1, color: "#f2c500" },
    thresholds: { basis: "halfwidth", warn: 0.3, danger: 0.1, fixed_mm: 300 },
  }],
  blink: { hz: 4, min: 0.18, max: 0.63 },
  alarm: { banner: true, chip: true, banner_min: "danger", sound: false },
  walls: true, ground: { enabled: true, warn_mm: 150, danger_mm: 80, hit_mm: 30 },
  overlay: { bbox: false },
};
const fence = new SafetyFence(new THREE.Scene());
fence.applyConfig(cfg);
out.groundPadFloor = fence.groundMesh ? bbox(fence.groundMesh) : null;   // 默认：铺在房间地坪
fence.setPadPlane({ y: PLATFORM_H + 0.004, size: PLATFORM_TOP_SIZE });
out.groundPadPlatform = fence.groundMesh ? bbox(fence.groundMesh) : null; // 站台上：铺在台面
fence.setPadPlane(null);
fence.dispose();

if (process.argv.includes("--json")) {
  console.log(JSON.stringify(out, null, 1));
} else {
  const f = (v) => (typeof v === "number" ? v.toFixed(3) : v);
  const line = (k, v) => console.log(`  ${k.padEnd(26, " ")} ${v}`);
  console.log("== 房间 ==");
  line("外沿 / 净高 (m)", `${f(out.room.size_m)} × ${f(out.room.size_m)} × ${f(out.room.height_m)}`);
  line("地板 / 天花板 y (m)", `${f(out.room.y[0])} … ${f(out.room.y[1])}`);
  line("灯带", `${out.room.lightStrips.count} 条 @ y=${f(out.room.lightStrips.atY)}`);

  console.log("\n== 工作区 / 站台 ==");
  line("工作区地坪 (m)", f(out.workFloor.size_m));
  line("台身尺寸 / 站台高 (m)", `${f(out.platform.size_m)} × ${f(out.platform.size_m)} × ${f(out.platform.height_m)}`);
  line("台面(含 7cm 台沿) (m)", f(out.platform.topSize_m));
  line("★ 台面(立足面) y (m)", f(out.platform.surfaceY));
  line("接触阴影 y (m)", f(out.platform.shadowY));
  line("调平脚数量", out.platform.feet);
  console.log("  —— 分层（自下而上）——");
  for (const L of out.platform.layers) {
    line(`  ${L.name.replace("platform-", "")}`,
      `y ${f(L.y[0])} … ${f(L.y[1])}   宽 ${f(L.span_x)}m`);
  }

  console.log("\n== 机器人（落地安装，已抬到台面） ==");
  line("整体高 (m)", f(out.robot.height_m));
  line("★ 底面 − 台面 (m)", `${f(out.robot.sitsOnPlatform)}   (≈0 = 正好坐在台面上)`);
  line("顶面高出台面 (m)", f(out.robot.topOverPlatform_m));
  line("顶上到天花板净空 (m)", f(out.robot.headroomToCeiling_m));

  console.log("\n== 地面报警垫 ==");
  if (out.groundPadFloor) line("默认铺地坪 y (m)", f(out.groundPadFloor.min[1]));
  if (out.groundPadPlatform) {
    line("★ 铺台面 y (m)", f(out.groundPadPlatform.min[1]));
    line("垫子边长 (m)", f(out.groundPadPlatform.max[0] - out.groundPadPlatform.min[0]));
  }
}
