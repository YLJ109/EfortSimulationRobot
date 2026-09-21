// 回归测试：用真实 GLB 顶点数据，端到端验证「官方数模 → 六轴可动」的挂载是否正确。
// 运行： cd frontend && node tools/verify_official_model.mjs
// 检查点：
//   1) 静止时世界包围盒 ≈ 官方数模原始尺寸 (高 0.810m) —— 证明 attach 没破坏外观
//   1b) 官方数模必须落在 manager.js「装配自检」的窗口内 —— 否则线上会被静默回退成
//       程序化模型（简化方块，用户会以为"官方模型没了/散架"）
//   2) 各连杆零件数分布正确
//   3) J2 转 30° 后模型确实运动
import * as THREE from "three";
import fs from "node:fs";

const GLB = "../public/models/robot_full.glb";
const buf = fs.readFileSync(new URL(GLB, import.meta.url));

// ---- 解析 GLB (JSON + BIN) ----
let js = null, binOff = -1, binLen = 0;
{
  let off = 12;
  while (off < buf.length) {
    const len = buf.readUInt32LE(off), type = buf.readUInt32LE(off + 4);
    if (type === 0x4e4f534a) js = JSON.parse(buf.slice(off + 8, off + 8 + len).toString("utf8"));
    else if (type === 0x004e4942) { binOff = off + 8; binLen = len; }
    off += 8 + len;
  }
}
const COMP = { 5120: Int8Array, 5121: Uint8Array, 5122: Int16Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array };

function accessor(aid) {
  const a = js.accessors[aid], bv = js.bufferViews[a.bufferView];
  const start = binOff + (bv.byteOffset || 0) + (a.byteOffset || 0);
  const stride = bv.byteStride || 0;
  const n = a.count, comp = COMP[a.componentType];
  const out = new Float32Array(n * 3);
  if (!stride || stride === 12) {
    const raw = new comp(buf.buffer, buf.byteOffset + start, n * 3);
    for (let i = 0; i < n * 3; i++) out[i] = raw[i];
  } else {
    for (let i = 0; i < n; i++) {
      const s = start + i * stride;
      for (let k = 0; k < 3; k++) out[i * 3 + k] = new comp(buf.buffer, buf.byteOffset + s + k * 4, 1)[0];
    }
  }
  return out;
}
function meshTris(mi) {
  const prim = js.meshes[mi].primitives[0];
  const pos = accessor(prim.attributes.POSITION);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  if (prim.indices !== undefined) {
    const ia = js.accessors[prim.indices];
    const idx = new COMP[ia.componentType](buf.buffer, buf.byteOffset + binOff + (js.bufferViews[ia.bufferView].byteOffset || 0) + (ia.byteOffset || 0), ia.count);
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(idx), 1));
  }
  g.computeBoundingBox();
  return g;
}

// ---- 官方数模零件 → 连杆 (与 manager.js 完全同一套规则) ----
const d1 = 376.0, a1 = 49.5932, a2 = 330.1834, a3 = 40.2714, d4 = 329.2414, d6 = 80.0;
const zJ2 = d1, zJ3 = d1 + a2, zJ4 = zJ3 + a3, xJ4 = a1, xJ5 = a1 + d4;
const NAME2LINK = { "转座": 1, "大臂": 2, "手腕体": 4, "手腕": 5, "法兰": 6 };
function fix(s) { try { return Buffer.from(s, "latin1").toString("gbk").split("^")[0]; } catch { return s; } }

const parts = [];
for (const n of js.nodes) {
  if (n.mesh === undefined) continue;
  const g = meshTris(n.mesh);
  const bb = g.boundingBox;
  parts.push({
    name: fix(n.name || ""),
    mesh: new THREE.Mesh(g),
    c: bb.getCenter(new THREE.Vector3()), s: bb.getSize(new THREE.Vector3()), maxZ: bb.max.z,
    maxDim: Math.max(bb.max.x - bb.min.x, bb.max.y - bb.min.y, bb.max.z - bb.min.z),
  });
}
function classify(p) {
  if (p.maxDim > 900) return -1;
  if (NAME2LINK[p.name] !== undefined) return NAME2LINK[p.name];
  if (p.maxZ <= zJ2 * 0.70) return 0;
  if (p.maxZ <= zJ2 * 1.25) return 1;
  if (p.c.x >= xJ5 - 20) return p.c.x >= xJ5 + 40 ? 6 : 5;
  if (p.c.z < zJ3) return 2;
  if (p.c.z < zJ4 && p.c.x < xJ4 + 120) return 3;
  return 4;
}

// ---- 搭链 (mm) 并挂载 ----
const deg2rad = (d) => (d * Math.PI) / 180;
const root = new THREE.Group();
root.rotation.x = -Math.PI / 2;
root.scale.setScalar(0.001);
const joints = []; let parent = root;
[[376, 49.5932, 90], [0.2852, 330.1834, 0], [0, 40.2714, 90], [329.2414, 0, -90], [0, 0, 90], [80, 0, 0]]
  .forEach(([d, a, al]) => {
    const jg = new THREE.Group(); parent.add(jg);
    const dz = new THREE.Group(); dz.position.z = d; jg.add(dz);
    const ax = new THREE.Group(); ax.position.x = a; dz.add(ax);
    const rx = new THREE.Group(); rx.rotation.x = deg2rad(al); ax.add(rx);
    joints.push(jg); parent = rx;
  });
const scene = new THREE.Scene();
scene.add(root);
root.updateMatrixWorld(true);

const nodes = [root, joints[0], joints[1], joints[2], joints[3], joints[4], joints[5]];
const dist = {};
// ★ 关键：零件必须先挂在"带 rotX(-90)+scale(0.001) 的 GLB 根"下面，attach() 才能正确补偿。
//   若零件没有父级(单独 clone 出来的)，attach 会算错 → 模型被放大/错位上千倍。
//   这正是本脚本要守护的回归点。
const glbRoot = new THREE.Group();
glbRoot.rotation.x = -Math.PI / 2;
glbRoot.scale.setScalar(0.001);
scene.add(glbRoot);
parts.forEach((p) => {
  const k = classify(p);
  if (k < 0) return;
  glbRoot.add(p.mesh);              // 模拟 GLB 装配关系
  nodes[k].attach(p.mesh);
  dist[k] = (dist[k] || 0) + 1;
});
glbRoot.visible = false;
root.updateMatrixWorld(true);

// ---- 检查 1: 世界包围盒 ≈ 官方原始尺寸 ----
const box = new THREE.Box3().setFromObject(root);
const size = box.getSize(new THREE.Vector3());
const FAIL = [];
const exp = { x: [0.60, 0.80], y: [0.70, 0.95], z: [0.20, 0.34] };   // 含末端装置后的预期范围
[["X(前后)", size.x, exp.x], ["Y(高度)", size.y, exp.y], ["Z(左右)", size.z, exp.z]].forEach(([nm, v, [lo, hi]]) => {
  const ok = v >= lo && v <= hi;
  if (!ok) FAIL.push(nm);
  console.log(`  ${nm} = ${v.toFixed(3)} m   (预期 ${lo}~${hi})  ${ok ? "PASS" : "FAIL"}`);
});
console.log(`  包围盒中心 y = ${((box.min.y + box.max.y) / 2).toFixed(3)} m (应≈0.40)`);

// ---- 检查 1b: 官方数模必须通过 manager.js 的「装配自检」窗口 ----
// buildArticulatedOfficial 装配完会自检，不达标就**抛错 → 回退程序化模型**。
// 阈值写歪的后果非常隐蔽：官方真机数模被静默换成简化方块模型，用户以为"官方模型没了/散架"。
// 这里用同一套窗口 + 落地安装的抬升量(PLATFORM_H)复算，把"官方数模一定通过"钉死。
const PLATFORM_H = 0.18;   // 与 three/cell.js 一致（站台台面高度）
const lifted = box.clone().translate(new THREE.Vector3(0, PLATFORM_H, 0));
const lsz = lifted.getSize(new THREE.Vector3());
console.log("\n  【自检窗口】官方数模必须通过，否则会被回退成程序化模型:");
[
  ["高", lsz.y, 0.70, 0.92, 0.810],
  ["前后跨度", lsz.x, 0.55, 0.85, 0.688],
  ["左右跨度", lsz.z, 0.15, 0.40, 0.256],
].forEach(([nm, v, lo, hi, expv]) => {
  const ok = v >= lo && v <= hi;
  if (!ok) FAIL.push("selfcheck-" + nm);
  console.log(`    ${nm} ${v.toFixed(3)}m ∈ [${lo}, ${hi}] (实测≈${expv})  ${ok ? "PASS" : "FAIL"}`);
});
const baseOk = Math.abs(lifted.min.y - PLATFORM_H) <= 0.03;
if (!baseOk) FAIL.push("selfcheck-底面");
console.log(`    底面 y ${lifted.min.y.toFixed(3)} ≈ 台面 ${PLATFORM_H}  ${baseOk ? "PASS" : "FAIL"}`);

// ---- 检查 1c: 「抬升顺序」语义 —— 抬升必须在 attach 之后 ----
// Object3D.attach() 保持对象**世界变换不变**：它会把 root 当时的 matrixWorld 一起补偿进
// 零件的局部矩阵。而零件的"原始世界坐标"来自 GLB（底面 y=0，与 root.position.y 无关），
// 所以**在 attach 之前设 root.position.y 等于白设** —— 抬升被补偿掉，模型仍停在 y=0，
// 下沉进 180mm 的站台里（表现为基座穿插/散架），而 root.position.y 读起来却是"已抬好"。
{
  const run = (liftBefore) => {
    const r = new THREE.Group();
    if (liftBefore) r.position.y = PLATFORM_H;
    const node = new THREE.Group();
    r.add(node);
    const s = new THREE.Scene();
    s.add(r); r.updateMatrixWorld(true);
    const part = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.2, 0.2));
    part.position.y = 0.1;                       // 底面正好在 y=0，模拟 GLB 原始坐标
    const src = new THREE.Group();               // 模拟 GLB 父级
    src.add(part);
    s.add(src); s.updateMatrixWorld(true);
    node.attach(part);                           // 与生产代码同一调用
    r.updateMatrixWorld(true);
    if (!liftBefore) r.position.y = PLATFORM_H;  // 正确做法：attach 之后再抬
    r.updateMatrixWorld(true);
    return new THREE.Box3().setFromObject(part).min.y;
  };
  const b = run(true);    // 提前抬升 → 应被抵消（仍是 0）
  const a = run(false);   // 之后抬升 → 应生效（0.18）
  const okB = Math.abs(b - 0) < 1e-6;
  const okA = Math.abs(a - PLATFORM_H) < 1e-6;
  if (!okB) FAIL.push("order-before");
  if (!okA) FAIL.push("order-after");
  console.log("\n  【抬升顺序】attach 保持世界变换 → 抬升必须放在 attach 之后:");
  console.log(`    attach 之前设 position.y → 抬升被抵消，零件底面仍 ${b.toFixed(3)} (应≈0.000)  ${okB ? "PASS" : "FAIL"}`);
  console.log(`    attach 之后设 position.y → 抬升生效，零件底面 ${a.toFixed(3)} (应≈0.180)  ${okA ? "PASS" : "FAIL"}`);
}

// ---- 检查 2: 连杆分布 ----
const want = { 0: 7, 1: 1, 2: 5, 3: 5, 4: 4, 5: 1, 6: 1 };
console.log("\n  连杆零件分布: " + JSON.stringify(dist));
Object.keys(want).forEach((k) => {
  const ok = (dist[k] || 0) === want[k];
  if (!ok) FAIL.push("link" + k);
  console.log(`    L${k}: ${dist[k] || 0} 件 (应 ${want[k]})  ${ok ? "PASS" : "FAIL"}`);
});

// ---- 检查 3: 关节转动带动模型 ----
function applyJoints(q) {
  const off = [0, 90, 0, 0, 0, 0];
  for (let i = 0; i < 6; i++) joints[i].rotation.z = deg2rad((q[i] || 0) + off[i]);
}
const before = new THREE.Box3().setFromObject(root).clone();
applyJoints([0, 40, 0, 0, 0, 0]);
root.updateMatrixWorld(true);
const after = new THREE.Box3().setFromObject(root);
const moved = before.min.distanceTo(after.min) > 0.05 || before.max.distanceTo(after.max) > 0.05;
if (!moved) FAIL.push("motion");
console.log(`  J2=+40° 后包围盒变化: ${moved ? "已运动 PASS" : "未运动 FAIL"}`);

console.log(`\n===== ${FAIL.length ? "FAIL: " + FAIL.join(", ") : "全部通过"} =====`);
process.exit(FAIL.length ? 1 : 0);
