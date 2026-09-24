// =====================================================================
// 关节高亮范围回归（headless，真实 THREE，无需 WebGL）
//
// ★ 为什么必须钉死这个：
//   曾经的实现用 `joints[i].traverse()` 判定高亮范围，而串联机器人里
//   J1 是 J2..J6 的**祖先节点** → 悬停 J1 全身亮、悬停 J2 亮 J2~J6。
//   这个 bug 不报错、不崩、headless 也不炸，只有肉眼在浏览器里才发现，
//   属于最典型的"静默回归"。这里用零件集合的包含关系把它锁死。
//
// 覆盖：
//   A. 每个关节都至少驱动一个零件（否则悬停它没有任何反馈）
//   B. J(i) 的零件集合与 J(i+1..6) 必须**无交集**（关键：祖先链绝不会串色）
//   C. 所有关节零件数之和 <= 模型总 mesh 数（基座等不属于任何关节）
//   D. 与"朴素 traverse 实现"对比：后者必然串色 → 反证本项目实现的正确性
//
// 运行： node tools/verify_highlight.mjs
// =====================================================================
import { buildRobot } from "../src/robot/robotModel.js";
import { buildOwnerMap, meshesOfJoint } from "../src/three/jointOwner.js";
import { FALLBACK_DH } from "../src/config.js";

let pass = 0, fail = 0;
function ok(name, cond, extra = "") {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (extra ? "   → " + extra : "")); }
}

console.log("== 关节高亮范围回归 ==");

const model = buildRobot(FALLBACK_DH, "floor");
const joints = model.joints || [];
ok("模型有 6 个关节", joints.length === 6, "joints=" + joints.length);

const sets = [];
for (let i = 0; i < joints.length; i++) sets.push(new Set(meshesOfJoint(model, i)));

// ---------- A. 每个关节都有可高亮的零件 ----------
const empty = [];
for (let i = 0; i < sets.length; i++) if (sets[i].size === 0) empty.push(i + 1);
ok("每个关节都至少驱动 1 个零件", empty.length === 0, "无零件的关节: J" + empty.join(", J"));

// ---------- B. 关节集合两两不相交（祖先链绝不串色） ----------
const overlaps = [];
for (let i = 0; i < sets.length; i++) {
  for (let j = i + 1; j < sets.length; j++) {
    let inter = 0;
    sets[i].forEach((m) => { if (sets[j].has(m)) inter++; });
    if (inter > 0) overlaps.push(`J${i + 1}∩J${j + 1}=${inter}`);
  }
}
ok("任意两个关节的零件集合不相交", overlaps.length === 0, overlaps.join(" "));

// 最关键的定向断言：J1 不能包含 J2..J6 的任何零件
const j1 = sets[0];
const leaked = [];
for (let j = 1; j < sets.length; j++) {
  let inter = 0;
  sets[j].forEach((m) => { if (j1.has(m)) inter++; });
  if (inter) leaked.push(`J${j + 1}(${inter})`);
}
ok("J1 不含下游关节的任何零件（原来的 bug 就是这个）", leaked.length === 0,
  "泄漏: " + leaked.join(" "));

// ---------- C. 归属总数不超过模型 mesh 总数 ----------
let totalMesh = 0;
model.root.traverse((o) => { if (o.isMesh) totalMesh++; });
const owner = buildOwnerMap(model);
const owned = owner.size;
ok("归属零件数 <= 模型总 mesh 数", owned <= totalMesh && owned > 0,
  `owned=${owned} total=${totalMesh}`);
ok("存在不属于任何关节的零件（基座等）", owned < totalMesh,
  `owned=${owned} total=${totalMesh}`);

// ---------- D. 反证：朴素 traverse 一定会串色 ----------
// 用"子树包含"实现一遍旧逻辑，断言它**确实**会把下游关节收进 J1。
function naiveCount(idx) {
  let n = 0;
  joints[idx].traverse((o) => { if (o.isMesh) n++; });
  return n;
}
const naiveJ1 = naiveCount(0);
const naiveJ2 = naiveCount(1);
ok("反证：朴素 traverse 会让 J1 覆盖下游（证明这个 bug 真实存在过）",
  naiveJ1 > j1.size && naiveJ1 >= naiveJ2,
  `naive J1=${naiveJ1} vs 正确 J1=${j1.size}; naive J2=${naiveJ2} vs 正确 J2=${sets[1].size}`);

// J2..J6 在朴素实现下会被 J1 全部吞掉 → 正确实现下必须各归各家
const chainOk = sets.slice(1).every((s) => s.size > 0);
ok("J2~J6 各自独立可高亮（不被 J1 吞并）", chainOk,
  "各关节零件数: " + sets.map((s) => s.size).join(","));

// 每个关节的零件数应随关节深入而递减或相当（越靠末端零件越少）
console.log("        各关节驱动零件数: " + sets.map((s, i) => `J${i + 1}=${s.size}`).join(" "));

console.log("");
console.log("结果: " + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
