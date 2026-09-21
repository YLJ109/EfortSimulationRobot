// 回归测试：安全围栏引擎（矩形 / 四点 / 圆形 · 多区域 · 阈值迁移）。
// 运行： cd frontend && node tools/verify_safety.mjs
//
// 只测纯几何与分级算法（three/safety.js 导出的纯函数），不依赖 DOM / WebGL。
import * as THREE from "three";
import {
  zonePolygon, polygonHalfspaces, clearanceToPolygon, clearanceToCircle,
  basisOf, classify, evaluateZone, classifyGround,
} from "../src/three/safety.js";

let pass = 0, fail = 0;
function ok(name, cond, extra = "") {
  if (cond) { pass++; console.log(`  PASS  ${name}${extra ? "  " + extra : ""}`); }
  else { fail++; console.log(`  FAIL  ${name}${extra ? "  " + extra : ""}`); }
}
function near(a, b, eps = 1e-6) { return Math.abs(a - b) <= eps; }

/** 半宽 h 的立方体 AABB（绕原点对称，高 0.8m）。 */
function box(h) {
  return new THREE.Box3(new THREE.Vector3(-h, 0, -h), new THREE.Vector3(h, 0.8, h));
}

const RECT = {
  id: "z1", name: "主工作区", shape: "rect", enabled: true,
  center: { x: 0, z: 0 }, half: { x: 0.82, z: 0.82 }, radius: 0.9,
  corners: [[-0.82, -0.82], [0.82, -0.82], [0.82, 0.82], [-0.82, 0.82]],
  height: 1.2, walls: true,
  thresholds: { basis: "halfwidth", warn: 0.30, danger: 0.10, fixed_mm: 300 },
};

console.log("== 1. 矩形区域：间隙与四档分级 ==");
{
  const c = (h) => clearanceToPolygon(box(h), polygonHalfspaces(zonePolygon(RECT)));
  ok("居中 0.35m 外廓 → 间隙 0.47m", near(c(0.35), 0.47, 1e-9), `实际 ${c(0.35).toFixed(4)}`);
  ok("基准 = 围栏半宽 0.82m", near(basisOf(RECT), 0.82, 1e-9), `实际 ${basisOf(RECT)}`);

  const s1 = evaluateZone(RECT, box(0.35));
  ok("余量 57% → safe", s1.state === "safe", `ratio=${s1.ratio.toFixed(3)}`);

  const s2 = evaluateZone(RECT, box(0.60));   // 间隙 0.22 → ratio 0.268
  ok("余量 26.8% → warn", s2.state === "warn", `ratio=${s2.ratio.toFixed(3)}`);

  const s3 = evaluateZone(RECT, box(0.80));   // 间隙 0.02 → ratio 0.024
  ok("余量 2.4% → danger", s3.state === "danger", `ratio=${s3.ratio.toFixed(3)}`);

  const s4 = evaluateZone(RECT, box(0.90));   // 间隙 -0.08
  ok("越界 → hit", s4.state === "hit", `clearance=${s4.clearance.toFixed(3)}`);

  // 阈值边界两侧各取一点（不取等号，避开浮点误差）
  const justAboveWarn = evaluateZone(RECT, box(0.55));   // 间隙 0.27 → 32.9%
  ok("余量 32.9%（>30%）→ safe", justAboveWarn.state === "safe", `ratio=${justAboveWarn.ratio.toFixed(3)}`);
  const justBelowWarn = evaluateZone(RECT, box(0.60));   // 间隙 0.22 → 26.8%
  ok("余量 26.8%（<30%）→ warn", justBelowWarn.state === "warn", `ratio=${justBelowWarn.ratio.toFixed(3)}`);
  const justAboveDanger = evaluateZone(RECT, box(0.73)); // 间隙 0.09 → 11.0%
  ok("余量 11.0%（>10%）→ warn", justAboveDanger.state === "warn", `ratio=${justAboveDanger.ratio.toFixed(3)}`);
  const justBelowDanger = evaluateZone(RECT, box(0.745));// 间隙 0.075 → 9.1%
  ok("余量 9.1%（<10%）→ danger", justBelowDanger.state === "danger", `ratio=${justBelowDanger.ratio.toFixed(3)}`);
}

console.log("== 2. 偏移中心：远离/接近双向可调 ==");
{
  const off = { ...RECT, center: { x: 0.3, z: 0 } };
  const s = evaluateZone(off, box(0.35));
  // 左边界 -0.52 → 间隙 0.17；右边界 1.12 → 间隙 0.77 → 取小 0.17
  ok("中心右移 → +X 侧余量变小", near(s.clearance, 0.17, 1e-9), `实际 ${s.clearance.toFixed(4)}`);
  const far = { ...RECT, center: { x: 0, z: 0 }, half: { x: 1.2, z: 1.2 } };
  const s2 = evaluateZone(far, box(0.35));
  ok("围栏变大 → 余量变大", s2.clearance > s.clearance, `${s2.clearance.toFixed(3)} > ${s.clearance.toFixed(3)}`);
}

console.log("== 3. 四点区域（旋转 45° 菱形）：法线方向正确 ==");
{
  const QUAD = {
    ...RECT, shape: "quad",
    corners: [[0, 1], [1, 0], [0, -1], [-1, 0]],   // 内切半径 1/√2
  };
  const hs = polygonHalfspaces(zonePolygon(QUAD));
  ok("生成 4 条边", hs.length === 4);
  // box(0.01) 的最近角是 (0.01,0.01)，到边 x+z=1 的距离 = (1-0.02)/√2
  const expect = (1 - 0.02) * Math.SQRT1_2;
  const c0 = clearanceToPolygon(box(0.01), hs);
  ok("中心小盒 → 间隙 = 内切半径 − 角点偏移", near(c0, expect, 1e-6),
    `实际 ${c0.toFixed(6)} 期望 ${expect.toFixed(6)}`);
  ok("基准 = 内切半径", near(basisOf(QUAD), Math.SQRT1_2, 1e-6), `实际 ${basisOf(QUAD).toFixed(6)}`);

  const outside = new THREE.Box3(new THREE.Vector3(0.9, 0, -0.05), new THREE.Vector3(1.1, 0.8, 0.05));
  const cOut = clearanceToPolygon(outside, hs);
  ok("越过 (1,0) 顶点 → 负间隙", cOut < 0, `实际 ${cOut.toFixed(4)}`);
  ok("越界 → hit", evaluateZone(QUAD, outside).state === "hit");

  const inside = new THREE.Box3(new THREE.Vector3(-0.2, 0, -0.2), new THREE.Vector3(0.2, 0.8, 0.2));
  ok("内部小盒 → safe", evaluateZone(QUAD, inside).state === "safe");
}

console.log("== 4. 圆形安全区：精确间隙 ==");
{
  const CIRC = { ...RECT, shape: "circle", radius: 0.9, center: { x: 0, z: 0 } };
  const c = clearanceToCircle(box(0.5), 0, 0, 0.9);
  ok("AABB 半宽 0.5 → 最远角 0.7071，间隙 0.1929",
    near(c, 0.9 - Math.hypot(0.5, 0.5), 1e-9), `实际 ${c.toFixed(6)}`);
  ok("基准 = 半径 0.9", near(basisOf(CIRC), 0.9, 1e-9));
  const s = evaluateZone(CIRC, box(0.5));
  ok("余量 21.4% → warn", s.state === "warn", `ratio=${s.ratio.toFixed(3)}`);
  ok("半径 1.5 → safe", evaluateZone({ ...CIRC, radius: 1.5 }, box(0.5)).state === "safe");
  ok("圆心外移 → 余量减小",
    evaluateZone({ ...CIRC, center: { x: 0.4, z: 0 } }, box(0.5)).clearance < s.clearance);
}

console.log("== 5. 阈值迁移（接近/远离双向） ==");
{
  const b = box(0.60);                       // ratio = 0.2683
  const base = evaluateZone(RECT, b);
  ok("warn=30% danger=10% → warn", base.state === "warn", `ratio=${base.ratio.toFixed(4)}`);

  const loose = evaluateZone({ ...RECT, thresholds: { ...RECT.thresholds, warn: 0.20 } }, b);
  ok("warn 调到 20%（更晚报警） → safe（远离）", loose.state === "safe");

  const tight = evaluateZone({ ...RECT, thresholds: { ...RECT.thresholds, warn: 0.60 } }, b);
  ok("warn 调到 60%（更早报警） → 仍 warn（接近）", tight.state === "warn");

  const dUp = evaluateZone({ ...RECT, thresholds: { ...RECT.thresholds, danger: 0.35 } }, b);
  ok("danger 调到 35% → danger", dUp.state === "danger");

  const fx = evaluateZone({ ...RECT, thresholds: { basis: "fixed", warn: 0.3, danger: 0.1, fixed_mm: 300 } }, b);
  ok("固定基准 300mm → ratio 0.733 → safe", fx.state === "safe", `ratio=${fx.ratio.toFixed(3)}`);

  const fx2 = evaluateZone({ ...RECT, thresholds: { basis: "fixed", warn: 0.9, danger: 0.8, fixed_mm: 300 } }, b);
  ok("固定基准 300mm + danger=80% → danger", fx2.state === "danger", `ratio=${fx2.ratio.toFixed(3)}`);
  const fx3 = evaluateZone({ ...RECT, thresholds: { basis: "fixed", warn: 0.9, danger: 0.5, fixed_mm: 300 } }, b);
  ok("同上 danger=50% → warn", fx3.state === "warn", `ratio=${fx3.ratio.toFixed(3)}`);
}

console.log("== 6. 多区域取最差 ==");
{
  const big = { ...RECT, id: "z1", name: "大区", half: { x: 1.2, z: 1.2 } };
  const small = { ...RECT, id: "z2", name: "小区", half: { x: 0.45, z: 0.45 } };
  const r1 = evaluateZone(big, box(0.35));
  const r2 = evaluateZone(small, box(0.35));
  ok("大区余量大 → safe", r1.state === "safe", `ratio=${r1.ratio.toFixed(3)}`);
  ok("小区余量小 → 更严重", r2.state === "warn" || r2.state === "danger",
    `state=${r2.state} ratio=${r2.ratio.toFixed(3)}`);
}

console.log("== 7. 地面碰撞分级（绿/黄/红/红闪，报警垫配色依据） ==");
{
  const g = { warn_mm: 100, danger_mm: 50, hit_mm: 20 };   // 阈值单位 mm，输入单位 m
  ok("离地 0.50m（>接近）→ safe", classifyGround(0.50, g) === "safe", `minY=0.500`);
  ok("离地 0.15m（>接近）→ safe", classifyGround(0.15, g) === "safe", `minY=0.150`);
  ok("离地 0.08m（<接近,>危险）→ warn", classifyGround(0.08, g) === "warn", `minY=0.080`);
  ok("离地 0.06m（<接近,>危险）→ warn", classifyGround(0.06, g) === "warn", `minY=0.060`);
  ok("离地 0.04m（<危险,>碰撞）→ danger", classifyGround(0.04, g) === "danger", `minY=0.040`);
  ok("离地 0.03m（<危险,>碰撞）→ danger", classifyGround(0.03, g) === "danger", `minY=0.030`);
  ok("离地 0.01m（<碰撞）→ hit", classifyGround(0.01, g) === "hit", `minY=0.010`);
  ok("离地 0.00m（触地）→ hit", classifyGround(0.0, g) === "hit");
  ok("离地 -0.05m（穿地）→ hit", classifyGround(-0.05, g) === "hit");
  // 边界取等号（<= 计入更严重档）
  ok("恰等于接近阈值 → warn", classifyGround(0.10, g) === "warn");
  ok("恰等于危险阈值 → danger", classifyGround(0.05, g) === "danger");
  ok("恰等于碰撞阈值 → hit", classifyGround(0.02, g) === "hit");
  ok("恰高于接近阈值 → safe", classifyGround(0.101, g) === "safe");
  // 阈值可调：三级整体收小
  const tight = { warn_mm: 60, danger_mm: 30, hit_mm: 10 };
  ok("阈值收到 60/30/10mm → 0.08m 变 safe", classifyGround(0.08, tight) === "safe");
  ok("同上 0.05m → warn", classifyGround(0.05, tight) === "warn");
  ok("同上 0.02m → danger", classifyGround(0.02, tight) === "danger");
  // 缺省阈值兜底（不传阈值也不报错）
  ok("缺省阈值兜底可用", ["safe", "warn", "danger", "hit"].includes(classifyGround(0.5, {})));
}

console.log(`\n结果: ${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);
