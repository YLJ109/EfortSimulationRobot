// 回归测试：在 Node 里复刻 three/manager.js 的 DH 运动链，与后端 kinematics.py 对拍。
// 用途：改了 DH 参数 / 运动链结构 / 关节轴后跑一遍，确认 3D 与前向运动学仍一致。
// 运行： cd frontend && node tools/verify_kinematics.mjs
import * as THREE from "three";

const deg2rad = (d) => (d * Math.PI) / 180;

// 与 config/robot.yaml 一致
const DH = [
  { d: 376.0, a: 49.5932, alpha: 90.0, theta_offset: 0.0 },
  { d: 0.2852, a: 330.1834, alpha: 0.0, theta_offset: 90.0 },
  { d: 0.0, a: 40.2714, alpha: 90.0, theta_offset: 0.0 },
  { d: 329.2414, a: 0.0, alpha: -90.0, theta_offset: 0.0 },
  { d: 0.0, a: 0.0, alpha: 90.0, theta_offset: 0.0 },
  { d: 80.0, a: 0.0, alpha: 0.0, theta_offset: 0.0 },
];

// 后端 fk_matrix 给出的法兰位置 (基座坐标, mm)
const REF = {
  "0,0,0,0,0,0": [458.8346, -0.2852, 746.4548],
  "0,30,0,0,0,0": [218.7792, -0.2852, 901.444],
  "45,-20,35,10,-40,25": [377.4398, 389.6647, 777.3292],
  "-60,50,-30,90,60,-120": [4.6819, -147.2438, 752.3685],
};

// ---- 复刻 manager.buildArticulatedOfficial 的链 (单位 mm, 根节点再缩到 m) ----
const root = new THREE.Group();
root.rotation.x = -Math.PI / 2;
root.scale.setScalar(0.001);

const joints = [];
let parent = root;
for (let i = 0; i < DH.length; i++) {
  const j = DH[i];
  const jg = new THREE.Group(); parent.add(jg);
  const dz = new THREE.Group(); dz.position.z = j.d; jg.add(dz);
  const ax = new THREE.Group(); ax.position.x = j.a; dz.add(ax);
  const rx = new THREE.Group(); rx.rotation.x = deg2rad(j.alpha); ax.add(rx);
  joints.push(jg);
  parent = rx;
}
const tcpNode = new THREE.Group();
parent.add(tcpNode);

function applyJoints(q) {
  for (let i = 0; i < joints.length; i++) {
    joints[i].rotation.z = deg2rad((q[i] || 0) + DH[i].theta_offset);
  }
}

// 世界坐标 -> 基座坐标(mm)：乘 root 的逆
function toBase(vec) {
  const m = root.matrixWorld.clone().invert();
  return vec.clone().applyMatrix4(m);
}

let fail = 0;
console.log("=== 1) TCP(法兰) 位置对拍：three 链 vs 后端 fk_matrix ===");
for (const [k, ref] of Object.entries(REF)) {
  const q = k.split(",").map(Number);
  applyJoints(q);
  root.updateMatrixWorld(true);
  const p = new THREE.Vector3();
  tcpNode.getWorldPosition(p);
  const b = toBase(p);
  const got = [b.x, b.y, b.z];
  const err = Math.max(...got.map((v, i) => Math.abs(v - ref[i])));
  const ok = err < 0.01;
  if (!ok) fail++;
  console.log(
    `  q=[${k}]  后端=(${ref.map((v) => v.toFixed(2)).join(", ")})  ` +
    `three=(${got.map((v) => v.toFixed(2)).join(", ")})  最大误差=${err.toFixed(4)}mm  ${ok ? "PASS" : "FAIL"}`
  );
}

console.log("\n=== 2) attach() 是否保持外观零偏移 (顶点世界位置不变) ===");
// 造一个顶点在基座坐标 (300, 10, 500) 的假零件, 挂到大臂(link2)
const geo = new THREE.BufferGeometry();
geo.setAttribute("position", new THREE.Float32BufferAttribute([300, 10, 500], 3));
const dummy = new THREE.Mesh(geo);
// 模拟 GLB: 零件原本挂在一个带 rotX(-90)+scale(0.001) 的旧根下
const oldRoot = new THREE.Group();
oldRoot.rotation.x = -Math.PI / 2;
oldRoot.scale.setScalar(0.001);
oldRoot.add(dummy);

const scene = new THREE.Scene();
scene.add(root);
applyJoints([0, 0, 0, 0, 0, 0]);
root.updateMatrixWorld(true);
oldRoot.updateMatrixWorld(true);

const before = new THREE.Vector3().fromBufferAttribute(geo.attributes.position, 0).applyMatrix4(dummy.matrixWorld);

joints[1].attach(dummy); // link2 = 大臂
root.updateMatrixWorld(true);
const after = new THREE.Vector3().fromBufferAttribute(geo.attributes.position, 0).applyMatrix4(dummy.matrixWorld);
const drift = before.distanceTo(after);
const ok2 = drift < 1e-6;
if (!ok2) fail++;
console.log(`  attach 前世界坐标=(${before.toArray().map((v) => v.toFixed(6)).join(", ")})`);
console.log(`  attach 后世界坐标=(${after.toArray().map((v) => v.toFixed(6)).join(", ")})`);
console.log(`  偏移=${drift.toExponential(2)} m  ${ok2 ? "PASS" : "FAIL"}`);

console.log("\n=== 3) attach 后关节转动是否带动零件 ===");
applyJoints([0, 30, 0, 0, 0, 0]);
root.updateMatrixWorld(true);
const moved = new THREE.Vector3().fromBufferAttribute(geo.attributes.position, 0).applyMatrix4(dummy.matrixWorld);
const movedMM = Math.abs(moved.clone().applyMatrix4(root.matrixWorld.clone().invert()).distanceTo(new THREE.Vector3(300, 10, 500)));
const ok3 = movedMM > 1;
if (!ok3) fail++;
console.log(`  J2=+30° 后顶点位移=${movedMM.toFixed(1)}mm  ${ok3 ? "PASS(已跟随运动)" : "FAIL(没动)"}`);

console.log(`\n===== 结果: ${fail === 0 ? "全部通过" : fail + " 项失败"} =====`);
process.exit(fail === 0 ? 0 : 1);
