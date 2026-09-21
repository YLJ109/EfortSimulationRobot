// =====================================================================
// 实验室环境几何回归（headless，纯 THREE，不碰渲染器）
//
// lab.js 是"造静态几何"的模块，没有 canvas/document 依赖，所以能在 node 里直接建出来，
// 用包围盒把下面这些**光看代码容易写错、在浏览器里也不一定看得出来**的事钉死：
//   1. 房间尺寸对不对（LAB_SIZE 见方、净高 LAB_HEIGHT）
//   2. 贴墙装饰（踢脚线/黄色安全带线/板缝）有没有被**埋进墙体**里
//   3. 实验室道具会不会**侵入中心工作区**（挡住主角）
//   4. 有没有道具穿出墙外 / 沉到地板下
//   5. 灯带是否真的在天花板下方、且在围栏高度之上
//   6. 3.6m 工作区地坪是否在房间内、不与墙干涉
//
// 运行： node tools/verify_lab.mjs
// =====================================================================
import * as THREE from "three";
import { buildLab, LAB_SIZE, LAB_HEIGHT, WALL_FADE_OPACITY } from "../src/three/lab.js";
import { FLOOR_SIZE } from "../src/three/cell.js";

let pass = 0, fail = 0;
function ok(name, cond, extra = "") {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (extra ? "   → " + extra : "")); }
}

const lab = buildLab();
lab.group.updateMatrixWorld(true);
const boxOf = (o) => new THREE.Box3().setFromObject(o);
const ALL = boxOf(lab.group);
const half = LAB_SIZE / 2;
const WALL_T = 0.1;
const inner = half - WALL_T;          // 墙内表面坐标（绝对值）
const f = (v) => Number(v).toFixed(3);
const PROPS = ["lab-bench", "lab-rack", "lab-ctrl", "lab-panel", "lab-door",
               "lab-extinguisher", "lab-robotcabinet", "lab-crates", "lab-pallets", "lab-cart"];

console.log("== 1. 房间尺寸 ==");
ok(`房间 ${LAB_SIZE}m 见方（外沿）`, Math.abs((ALL.max.x - ALL.min.x) - LAB_SIZE) < 0.02,
  `dx=${f(ALL.max.x - ALL.min.x)}`);
ok(`房间 ${LAB_SIZE}m 进深`, Math.abs((ALL.max.z - ALL.min.z) - LAB_SIZE) < 0.02,
  `dz=${f(ALL.max.z - ALL.min.z)}`);
ok(`净高 ${LAB_HEIGHT}m`, Math.abs(ALL.max.y - LAB_HEIGHT) < 0.06, `maxY=${f(ALL.max.y)}`);
ok("地面在 y≈0 附近（不沉下去）", ALL.min.y > -0.03, `minY=${f(ALL.min.y)}`);
ok("房间明显大于工作区（现场感）", LAB_SIZE >= FLOOR_SIZE * 3,
  `${LAB_SIZE} vs ${FLOOR_SIZE}`);

console.log("\n== 2. 地面 / 四面墙 ==");
{
  const fl = lab.group.getObjectByName("lab-floor");
  ok("房间地坪存在", !!fl);
  if (fl) {
    const b = boxOf(fl);
    ok("地坪边长 = 内表面间距（正好铺到墙根）",
      Math.abs((b.max.x - b.min.x) - 2 * inner) < 0.02,
      `dx=${f(b.max.x - b.min.x)} inner*2=${f(2 * inner)}`);
  }
  const walls = [];
  lab.group.traverse((o) => { if (o.name === "lab-wall") walls.push(o); });
  ok("四面墙", walls.length === 4, `walls=${walls.length}`);
  const axes = walls.map((w) => {
    const b = boxOf(w);
    return (b.max.x - b.min.x) < (b.max.z - b.min.z) ? "x" : "z";
  }).sort().join("");
  ok("东西两面 + 南北两面", axes === "xxzz", axes);
  // 相机可拉到房间外(maxDistance 9 > 半宽 7)：墙必须是**单面**渲染，
  // 否则近侧墙会变成一堵挡死画面的灰板（DoubleSide 会把背面也画出来）
  ok("墙为单面渲染（拉到房间外不被近墙挡死）",
    walls.every((w) => w.material.side === THREE.FrontSide),
    walls.map((w) => w.material.side).join(","));
}

console.log("\n== 3. ★ 贴墙装饰必须在室内（不能被埋进墙体） ==");
{
  const check = (name) => {
    let n = 0, bad = 0, worst = "";
    lab.group.traverse((o) => {
      if (o.name !== name) return;
      n++;
      const b = boxOf(o);
      const far = Math.max(Math.abs(b.min.x), Math.abs(b.max.x),
                           Math.abs(b.min.z), Math.abs(b.max.z));
      if (far > inner + 1e-6) { bad++; worst = f(far); }
    });
    ok(`${name} 共 ${n} 件且全部贴在内表面内侧(≤${f(inner)})`, n > 0 && bad === 0,
      `n=${n} bad=${bad} worst=${worst}`);
  };
  check("lab-skirt");   // 踢脚线
  check("lab-band");    // 黄色安全带线
  check("lab-seam");    // 竖向板缝
}

console.log("\n== 4. ★ 实验室道具不得侵入中心工作区 ==");
{
  const CLEAR = 2.4;    // 工作区 3.6m(半宽 1.8) + 0.6m 余量
  for (const p of PROPS) {
    const o = lab.group.getObjectByName(p);
    if (!o) { ok(`${p} 存在`, false); continue; }
    const b = boxOf(o);
    const intrude = b.min.x > -CLEAR && b.max.x < CLEAR && b.min.z > -CLEAR && b.max.z < CLEAR;
    ok(`${p} 贴墙、不占中心区`, !intrude,
      `box x[${f(b.min.x)},${f(b.max.x)}] z[${f(b.min.z)},${f(b.max.z)}]`);
  }
  const CENTER = new THREE.Box3(new THREE.Vector3(-1.6, 0, -1.6), new THREE.Vector3(1.6, 3.0, 1.6));
  const hits = [];
  lab.group.traverse((o) => {
    if (!o.isMesh) return;
    if (o.name === "lab-floor" || o.name === "lab-ceiling" || o.name === "lab-wall" ||
        o.name === "lab-skirt" || o.name === "lab-band" || o.name === "lab-seam" ||
        o.name.startsWith("lab-light")) return;
    if (boxOf(o).intersectsBox(CENTER)) hits.push(o.name + "/" + (o.parent.name || ""));
  });
  ok("中心 3.2×3.2m 空间内无道具挡视线", hits.length === 0, hits.join(", "));
}

console.log("\n== 5. 道具不穿墙 / 不沉地 ==");
{
  let out = 0, sunk = 0, worstOut = "", worstSunk = "";
  for (const p of PROPS) {
    const o = lab.group.getObjectByName(p);
    if (!o) continue;
    const b = boxOf(o);
    const far = Math.max(Math.abs(b.min.x), Math.abs(b.max.x), Math.abs(b.min.z), Math.abs(b.max.z));
    if (far > inner + 0.02) { out++; worstOut = p + ":" + f(far); }
    if (b.min.y < -0.03) { sunk++; worstSunk = p + ":" + f(b.min.y); }
  }
  ok("无道具穿出墙外", out === 0, `out=${out} ${worstOut}`);
  ok("无道具沉到地板下", sunk === 0, `sunk=${sunk} ${worstSunk}`);

  // 道具之间也不能互相穿插（大房间里道具多、手动摆位极易重叠）
  const clashes = [];
  for (let i = 0; i < PROPS.length; i++) {
    for (let j = i + 1; j < PROPS.length; j++) {
      const a = lab.group.getObjectByName(PROPS[i]);
      const b = lab.group.getObjectByName(PROPS[j]);
      if (!a || !b) continue;
      if (boxOf(a).intersectsBox(boxOf(b))) clashes.push(`${PROPS[i]}×${PROPS[j]}`);
    }
  }
  ok("道具之间互不穿插", clashes.length === 0, clashes.join(", "));
}

console.log("\n== 6. 照明 ==");
{
  const strips = [], lights = [];
  lab.group.traverse((o) => {
    if (o.name === "lab-lightstrip") strips.push(boxOf(o));
    if (o.isPointLight) lights.push(o);
  });
  ok("5 条天花板灯带", strips.length === 5, `strips=${strips.length}`);
  ok("灯带都在天花板下方且在围栏(1.2m)之上",
    strips.every((b) => b.min.y > 2.5 && b.max.y < LAB_HEIGHT + 0.01),
    strips.map((b) => f(b.min.y)).join(","));
  ok("点光源数量合理(3 盏，别拖性能)", lights.length === 3, String(lights.length));
  ok("至少一条灯带压在中心工作区上方（照亮机器人）",
    strips.some((b) => b.min.x < 0 && b.max.x > 0));
}

console.log("\n== 7. 工作区地坪与房间自洽 ==");
{
  ok(`工作区地板(${FLOOR_SIZE}m)位于房间内`, FLOOR_SIZE < 2 * inner - 0.2);
  ok("工作区地板与墙之间留足通行宽度(>2m)", inner - FLOOR_SIZE / 2 > 2.0,
    f(inner - FLOOR_SIZE / 2));
  // 3.6m 工作区 + 房间地坪 → 地面要有"房间级"的余量，否则房间白加
  ok("房间地坪面积 ≥ 工作区的 4 倍", (2 * inner) ** 2 >= 4 * FLOOR_SIZE ** 2,
    `${f((2 * inner) ** 2)} vs ${f(4 * FLOOR_SIZE ** 2)}`);
}

console.log("\n== 8. ★ 智能剖切：挡在相机前面的墙/天花板自动淡出 ==");
{
  const wallAt = (axis, s) => {
    let found = null;
    lab.group.traverse((o) => {
      if (o.name !== "lab-wall") return;
      const p = axis === "z" ? o.position.z : o.position.x;
      if (Math.sign(p) === s) found = o;
    });
    return found;
  };
  const wallOf = { "z-": wallAt("z", -1), "z+": wallAt("z", 1),
                   "x-": wallAt("x", -1), "x+": wallAt("x", 1) };
  const ceilMesh = lab.group.getObjectByName("lab-ceiling");
  const o = (m) => Number(m.material.opacity);
  // 收敛到稳态（每步 dt=0.1，k≈0.5 → 几十步足够）
  const fadeTo = (x, y, z) => {
    const cam = new THREE.PerspectiveCamera();
    cam.position.set(x, y, z);
    for (let i = 0; i < 80; i++) lab.updateVisibility(cam, 0.1);
  };
  const allWalls = ["z-", "z+", "x-", "x+"];

  fadeTo(1.7, 1.35, 1.75);            // 室内
  ok("相机在室内 → 四面墙全实", allWalls.every((k) => o(wallOf[k]) > 0.99),
    allWalls.map((k) => k + ":" + f(o(wallOf[k]))).join(" "));
  ok("相机在室内 → 天花板实", o(ceilMesh) > 0.99, f(o(ceilMesh)));

  fadeTo(inner + 3, 1.35, 0);         // 站到 +X 墙外面
  ok("相机在 +X 墙外 → +X 墙变半透明玻璃",
    o(wallOf["x+"]) < 0.35 && o(wallOf["x+"]) > 0.1, f(o(wallOf["x+"])));
  ok("★ 淡出后四边墙仍看得见（不是消失）", o(wallOf["x+"]) > 0.1,
    f(o(wallOf["x+"])));
  ok("其余三面仍实（只淡挡视线的那面）",
    ["z-", "z+", "x-"].every((k) => o(wallOf[k]) > 0.99),
    ["z-", "z+", "x-"].map((k) => k + ":" + f(o(wallOf[k]))).join(" "));

  fadeTo(0, LAB_HEIGHT + 3, 0);       // 升到屋顶上方 —— 用户反馈的场景
  ok("相机在屋顶上方 → 天花板变半透明（看得到下面）",
    o(ceilMesh) < 0.35 && o(ceilMesh) > 0.1, f(o(ceilMesh)));
  ok("相机在屋顶上方 → 四面墙仍是实墙（从上往下看四边墙都在）",
    allWalls.every((k) => o(wallOf[k]) > 0.99),
    allWalls.map((k) => k + ":" + f(o(wallOf[k]))).join(" "));

  fadeTo(inner + 3, LAB_HEIGHT + 3, 0);   // 上方 + 外侧（最容易挡死画面的机位）
  ok("相机在上方外侧 → 近侧墙 + 天花板同时变半透明且都还在",
    o(wallOf["x+"]) > 0.1 && o(wallOf["x+"]) < 0.35 &&
    o(ceilMesh) > 0.1 && o(ceilMesh) < 0.35,
    `x+=${f(o(wallOf["x+"]))} ceil=${f(o(ceilMesh))}`);

  fadeTo(1.7, 1.35, 1.75);            // 回到室内
  ok("相机回到室内 → 全部恢复实体",
    allWalls.every((k) => o(wallOf[k]) > 0.99) && o(ceilMesh) > 0.99,
    allWalls.map((k) => k + ":" + f(o(wallOf[k]))).join(" ") + " ceil:" + f(o(ceilMesh)));
  ok("半透明阈值合理（既透得出、又看得见墙）",
    WALL_FADE_OPACITY >= 0.15 && WALL_FADE_OPACITY <= 0.35, String(WALL_FADE_OPACITY));
}

lab.dispose();
console.log(`\n结果: ${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);
