// =====================================================================
// 守卫：模拟摆放物体的碰撞核心（AABB 判定 / 推出），纯函数，node 直接跑。
// 无需 WebGL / 无需 document。断言通过则退出 0，否则抛错退出非 0。
// 运行：node tools/verify_simobjects.mjs   （frontend 目录下）
// =====================================================================
import { aabbOverlapXZ, aabbOverlap, resolveXZ } from "../src/three/simObjects.js";

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; }
  else { fail++; console.error("  ✗ " + msg); }
}

// ---- aabbOverlapXZ（只看 X/Z）----
const A = { cx: 0, cz: 0, hw: 0.1, hd: 0.1 };
const near = { cx: 0.25, cz: 0, hw: 0.1, hd: 0.1 };   // 0.25 > 半宽和0.2 → 不重叠
const overlap = { cx: 0.18, cz: 0, hw: 0.1, hd: 0.1 }; // 0.18 < 0.2 → 重叠
assert(!aabbOverlapXZ(A, near), "XZ 不重叠时判定为 false");
assert(aabbOverlapXZ(A, overlap), "XZ 重叠时判定为 true");

// ---- aabbOverlap（含 Y，用于体积判定）----
const B = { cx: 0, cy: 0, cz: 0, hw: 0.1, hh: 0.05, hd: 0.1 };
const high = { cx: 0, cy: 1, cz: 0, hw: 0.1, hh: 0.05, hd: 0.1 };
assert(!aabbOverlap(B, high), "Y 相距很远不重叠");
assert(aabbOverlap(B, { cx: 0, cy: 0.08, cz: 0, hw: 0.1, hh: 0.05, hd: 0.1 }), "Y 方向有重叠");

// ---- resolveXZ：把重叠的 a 沿最短穿透轴推开 ----
const r1 = { cx: 0, cz: 0, hw: 0.1, hd: 0.1 };
const r2 = { cx: 0.15, cz: 0, hw: 0.1, hd: 0.1 };   // X 方向重叠 0.05，Z 无重叠
assert(resolveXZ(r1, r2), "推出发生位移");
assert(!aabbOverlapXZ(r1, r2), "推出后不再重叠");
assert(Math.abs(r1.cx - r2.cx) >= r1.hw + r2.hw - 1e-6, "推出后间隔 ≥ 半宽和");

// ---- 对角重叠：推到最后互不重叠 ----
const d1 = { cx: 0, cz: 0, hw: 0.1, hd: 0.1 };
const d2 = { cx: 0.02, cz: 0.06, hw: 0.1, hd: 0.1 };
resolveXZ(d1, d2);
assert(!aabbOverlapXZ(d1, d2), "对角重叠被完全推开");

// ---- 完全不重叠时 resolve 不改动 ----
const s1 = { cx: 0, cz: 0, hw: 0.1, hd: 0.1 };
const s2 = { cx: 0.5, cz: 0, hw: 0.1, hd: 0.1 };
assert(!resolveXZ(s1, s2), "无重叠时 resolve 返回 false 且不改动");

// ---- 三体顺序推出：迭代分离直到两两不再重叠 ----
const BX = (o) => ({ cx: o.cx, cz: o.cz, hw: 0.1, hd: 0.1 });
const bodies = [{ cx: 0, cz: 0 }, { cx: 0.18, cz: 0 }, { cx: 0.34, cz: 0 }].map(BX);
for (let iter = 0; iter < 16; iter++) {
  for (let i = 0; i < bodies.length; i++) {
    for (let j = i + 1; j < bodies.length; j++) resolveXZ(bodies[i], bodies[j]);
  }
}
let ok = true;
for (let i = 0; i < bodies.length; i++)
  for (let j = i + 1; j < bodies.length; j++)
    if (aabbOverlapXZ(bodies[i], bodies[j])) ok = false;
assert(ok, "三体迭代推出后两两不再重叠");

console.log(`\n碰撞核心守卫：${pass} passed, ${fail} failed`);
if (fail) { console.error("模拟物体碰撞守卫失败"); process.exit(1); }
console.log("碰撞核心守卫通过");