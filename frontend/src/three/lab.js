// =====================================================================
// 实验室环境：把场景包进一间真实实验室里。
//
// 全部由低多边形几何构成（无外部资源、无渲染器依赖），只造静态物件：
//   - 房间外壳：地面 + 四面墙(竖向板缝/踢脚线/黄色安全带线) + 天花板 + 灯带
//   - 实验室道具：工作台×2、设备机柜×2、控制柜+HMI、配电柜、标准门、灭火器箱、
//     机器人电柜、料箱堆、木托盘+工件箱、工具车 —— 都贴墙摆放，不遮挡中心工作区
//   - 天花板点光源补光（配合 manager 的环境贴图/主光/补光）
//
// ★ 尺寸：14m × 14m、净高 4.2m。中心 3.6m 是机器人工作区(three/cell.js)。
// ★ 定位规则：`wallPos = half − T/2`（墙**外沿**正好 ±half → 外沿尺寸严格 = LAB_SIZE），
//   `inner = half − T`（内表面），贴墙横条长度 `span = 2*inner`（两端正好抵住邻墙）。
// ★★ **智能剖切（updateVisibility）**：相机转到房间外/天花板上方时，凡是"挡在相机与
//   工作区之间"的那面墙（或天花板）会平滑淡成**半透明玻璃**（`WALL_FADE_OPACITY`）——
//   既看得到墙的轮廓（四边墙始终在），又能透视看到里面的机器人；相机回到室内时恢复实墙。
//   每帧由 manager.renderFrame() 调用。
// =====================================================================
import * as THREE from "three";

export const LAB_SIZE = 14.0;     // 房间**外沿**边长(m)
export const LAB_HEIGHT = 4.2;    // 净高(m) —— 抬高顶棚，给"站在站台上"的机器人留够净空
// ★ 挡视线时淡到"半透明玻璃"而**不是消失**：四边墙必须始终看得见（用户明确要求），
//   同时又能看清里面 —— 这个值是"还看得见轮廓"与"能透视"的折中。
export const WALL_FADE_OPACITY = 0.22;
const T = 0.1;                    // 墙厚(m)
const SEAM_STEP = 1.6;            // 竖向板缝间距(m)

// ---- 材质模板（设备/道具用共享的，外壳每面墙 clone 自己一份以便各自淡出） ----
// ★ 墙与天花板：`transparent:true + depthWrite:true`。这样**不透明物体（机器人/道具）
//   先画且写深度**，墙的像素若在它们之后（更远）就会被深度测试丢弃 → 墙再透明也不会
//   "糊"在机器人上，淡出过程干净。
function makeMats() {
  return {
    wall: new THREE.MeshStandardMaterial({ color: 0xc9ced4, metalness: 0.02, roughness: 0.9 }),
    wallDark: new THREE.MeshStandardMaterial({ color: 0x8a9199, metalness: 0.06, roughness: 0.8 }),
    floor: new THREE.MeshStandardMaterial({ color: 0x60666e, metalness: 0.1, roughness: 0.82 }),
    skirt: new THREE.MeshStandardMaterial({ color: 0x4e555e, metalness: 0.12, roughness: 0.75 }),
    band: new THREE.MeshStandardMaterial({ color: 0xe0b400, metalness: 0.15, roughness: 0.6, emissive: 0x3a3000, emissiveIntensity: 0.25 }),
    ceil: new THREE.MeshStandardMaterial({ color: 0xdcdfe3, metalness: 0.02, roughness: 0.95 }),
    strip: new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0xffffff, emissiveIntensity: 2.2, roughness: 0.4 }),
    benchTop: new THREE.MeshStandardMaterial({ color: 0x7f868f, metalness: 0.1, roughness: 0.5 }),
    dark: new THREE.MeshStandardMaterial({ color: 0x40464e, metalness: 0.2, roughness: 0.7 }),
    cab: new THREE.MeshStandardMaterial({ color: 0xb4bac1, metalness: 0.15, roughness: 0.55 }),
    crate: new THREE.MeshStandardMaterial({ color: 0x53617a, metalness: 0.05, roughness: 0.8 }),
    wood: new THREE.MeshStandardMaterial({ color: 0x9a7c58, metalness: 0.02, roughness: 0.85 }),
    door: new THREE.MeshStandardMaterial({ color: 0xaeb4bb, metalness: 0.1, roughness: 0.6 }),
    red: new THREE.MeshStandardMaterial({ color: 0xb2352f, metalness: 0.1, roughness: 0.55 }),
    ledG: new THREE.MeshStandardMaterial({ color: 0x2ecc71, emissive: 0x2ecc71, emissiveIntensity: 1.6 }),
    ledA: new THREE.MeshStandardMaterial({ color: 0xf2c500, emissive: 0xf2c500, emissiveIntensity: 1.6 }),
    hmi: new THREE.MeshStandardMaterial({ color: 0x0d1b24, emissive: 0x2fa8d5, emissiveIntensity: 0.9, roughness: 0.3 }),
  };
}

function box(w, h, d, mat, x, y, z, name) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y, z);
  if (name) m.name = name;
  return m;
}

/** 复制一份"可淡出"的外壳材质（透明 + 写深度，见文件头说明）。 */
function fadeable(src) {
  const m = src.clone();
  m.transparent = true;
  m.depthWrite = true;
  return m;
}

/**
 * @param {{size?:number, height?:number}} opt
 * @returns {{group: THREE.Group, updateVisibility:(cam:THREE.Camera, dt?:number)=>void, dispose:()=>void}}
 */
export function buildLab(opt = {}) {
  const S = opt.size || LAB_SIZE;
  const H = opt.height || LAB_HEIGHT;
  const half = S / 2;
  const wallPos = half - T / 2;
  const inner = half - T;            // 墙内表面坐标（绝对值）
  const span = 2 * inner;            // 贴墙横条长度：正好顶到邻墙内表面
  const group = new THREE.Group();
  group.name = "lab";
  const M = makeMats();
  const geos = [];
  const shells = [];                 // [{ test(camPos), mats:[], op }] 可淡出的外壳件
  const cloneMats = [];              // 需要 dispose 的 clone 材质

  // ---------------- 地面（整间房的基层地坪，永不淡出） ----------------
  const fg = new THREE.PlaneGeometry(span, span);
  const floor = new THREE.Mesh(fg, M.floor);
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -0.012;
  floor.name = "lab-floor";
  group.add(floor); geos.push(fg);

  // ---------------- 四面墙（各自一份材质 → 可单独淡出） ----------------
  for (const w of [{ axis: "z", s: -1 }, { axis: "z", s: 1 },
                   { axis: "x", s: -1 }, { axis: "x", s: 1 }]) {
    const isZ = w.axis === "z";
    const wallM = fadeable(M.wall);
    const skirtM = fadeable(M.skirt);
    const bandM = fadeable(M.band);
    const seamM = fadeable(M.wallDark);
    cloneMats.push(wallM, skirtM, bandM, seamM);

    const wg = isZ ? new THREE.BoxGeometry(S, H, T) : new THREE.BoxGeometry(T, H, S);
    const wall = new THREE.Mesh(wg, wallM);
    wall.name = "lab-wall";
    if (isZ) wall.position.set(0, H / 2, w.s * wallPos);
    else wall.position.set(w.s * wallPos, H / 2, 0);
    group.add(wall); geos.push(wg);

    // 踢脚线 + 黄色安全带线（现场标准高度 ~0.9m），长度 span → 两端抵住邻墙内表面
    for (const st of [
      { y: 0.06, h: 0.12, d: 0.05, mat: skirtM, name: "lab-skirt" },
      { y: 0.91, h: 0.12, d: 0.03, mat: bandM, name: "lab-band" },
    ]) {
      const g = isZ ? new THREE.BoxGeometry(span, st.h, st.d) : new THREE.BoxGeometry(st.d, st.h, span);
      const m = new THREE.Mesh(g, st.mat);
      m.name = st.name;
      const off = w.s * (inner - st.d / 2);
      if (isZ) m.position.set(0, st.y, off);
      else m.position.set(off, st.y, 0);
      group.add(m); geos.push(g);
    }

    // 竖向板缝
    const n = Math.floor(inner / SEAM_STEP);
    for (let i = -n; i <= n; i++) {
      if (i === 0) continue;
      const along = i * SEAM_STEP;
      if (Math.abs(along) > inner - 0.15) continue;
      const g = isZ
        ? new THREE.BoxGeometry(0.024, H - 0.14, 0.018)
        : new THREE.BoxGeometry(0.018, H - 0.14, 0.024);
      const m = new THREE.Mesh(g, seamM);
      m.name = "lab-seam";
      const off = w.s * (inner - 0.009);
      if (isZ) m.position.set(along, H / 2 - 0.02, off);
      else m.position.set(off, H / 2 - 0.02, along);
      group.add(m); geos.push(g);
    }

    // 相机在这面墙的**外侧** → 它挡在相机与工作区之间 → 淡出
    shells.push({
      test: (p) => (isZ ? p.z : p.x) * w.s > inner + 0.06,
      mats: [wallM, skirtM, bandM, seamM],
      op: 1,
    });
  }

  // ---------------- 天花板（相机升到屋顶以上时淡出） + 灯带 ----------------
  const ceilM = fadeable(M.ceil);
  cloneMats.push(ceilM);
  const cg = new THREE.PlaneGeometry(span, span);
  const ceil = new THREE.Mesh(cg, ceilM);
  ceil.rotation.x = Math.PI / 2;
  ceil.position.y = H;
  ceil.name = "lab-ceiling";
  group.add(ceil); geos.push(cg);
  shells.push({ test: (p) => p.y > H + 0.06, mats: [ceilM], op: 1 });

  const stripZs = [-4.8, -2.4, 0, 2.4, 4.8];
  const stripLen = 8.4;
  for (const sz of stripZs) {
    const s1 = box(stripLen, 0.04, 0.2, M.strip, 0, H - 0.035, sz, "lab-lightstrip");
    const s2 = box(stripLen + 0.1, 0.03, 0.26, M.skirt, 0, H - 0.05, sz, "lab-lightframe");
    group.add(s1, s2); geos.push(s1.geometry, s2.geometry);
  }

  // ---------------- 实验室道具（全部贴墙，不遮挡中心工作区） ----------------
  // 1) 工作台 ×2 + 台上仪器（-X 墙）
  const bench = new THREE.Group(); bench.name = "lab-bench";
  for (const bz of [-3.0, 0.6]) {
    bench.add(box(0.75, 0.06, 2.2, M.benchTop, -inner + 0.40, 0.9, bz));
    for (const [dz, dx] of [[-0.95, 0.28], [0.95, 0.28], [-0.95, -0.28], [0.95, -0.28]]) {
      bench.add(box(0.05, 0.87, 0.05, M.dark, -inner + 0.40 + dx, 0.435, bz + dz));
    }
  }
  bench.add(box(0.34, 0.2, 0.46, M.dark, -inner + 0.37, 1.03, -3.5));     // 仪器
  bench.add(box(0.02, 0.02, 0.02, M.ledG, -inner + 0.20, 1.14, -3.32));   // 电源灯
  bench.add(box(0.26, 0.16, 0.34, M.cab, -inner + 0.37, 1.01, 0.35));     // 小显示器
  bench.add(box(0.30, 0.16, 0.36, M.dark, -inner + 0.37, 1.0, 1.05));     // 电源模块
  bench.add(box(0.02, 0.02, 0.02, M.ledA, -inner + 0.20, 1.09, 1.05));
  group.add(bench);

  // 2) 设备机柜 ×2（+Z 墙，19" 机架风格）
  const rack = new THREE.Group(); rack.name = "lab-rack";
  for (const rx of [-3.6, 3.4]) {
    rack.add(box(1.5, 2.1, 0.62, M.cab, rx, 1.05, inner - 0.33));
    rack.add(box(1.4, 1.95, 0.03, M.dark, rx, 1.05, inner - 0.645));
    for (let i = 0; i < 4; i++) {
      rack.add(box(0.05, 0.05, 0.02, i % 2 ? M.ledA : M.ledG,
        rx - 0.45 + i * 0.3, 1.88, inner - 0.665));
    }
  }
  group.add(rack);

  // 3) 控制柜 + HMI 屏（+X 墙）
  const ctrl = new THREE.Group(); ctrl.name = "lab-ctrl";
  ctrl.add(box(0.55, 1.3, 1.0, M.cab, inner - 0.30, 0.8, -3.0));
  ctrl.add(box(0.03, 0.55, 0.66, M.dark, inner - 0.585, 1.4, -3.0));
  ctrl.add(box(0.016, 0.4, 0.55, M.hmi, inner - 0.607, 1.4, -3.0));
  group.add(ctrl);

  // 4) 配电柜（+X 墙，深灰 + 一排断路器）
  const panel = new THREE.Group(); panel.name = "lab-panel";
  panel.add(box(0.35, 1.6, 1.2, M.dark, inner - 0.20, 1.0, 2.0));
  panel.add(box(0.02, 1.4, 1.05, M.cab, inner - 0.385, 1.0, 2.0));
  for (let i = 0; i < 5; i++) {
    panel.add(box(0.01, 0.14, 0.1, M.crate, inner - 0.40, 0.55 + i * 0.24, 1.65));
    panel.add(box(0.01, 0.14, 0.1, M.crate, inner - 0.40, 0.55 + i * 0.24, 2.35));
  }
  group.add(panel);

  // 5) 标准门 + 把手 + EXIT 标识（-Z 墙）
  const door = new THREE.Group(); door.name = "lab-door";
  door.add(box(1.1, 2.2, 0.07, M.door, -1.8, 1.1, -inner + 0.025));
  door.add(box(0.05, 0.26, 0.05, M.dark, -1.32, 1.05, -inner + 0.09));
  door.add(box(0.44, 0.15, 0.03, M.ledG, -1.8, 2.42, -inner + 0.045));
  group.add(door);

  // 6) 灭火器箱（-Z 墙）
  const ext = new THREE.Group(); ext.name = "lab-extinguisher";
  ext.add(box(0.36, 0.62, 0.26, M.red, 3.6, 0.31, -inner + 0.15));
  ext.add(box(0.24, 0.08, 0.03, M.cab, 3.6, 0.45, -inner + 0.29));
  group.add(ext);

  // 7) 机器人电柜（-Z 墙，门旁 —— 与真实现场呼应）
  const ec = new THREE.Group(); ec.name = "lab-robotcabinet";
  ec.add(box(0.65, 1.7, 1.0, M.cab, -inner + 0.36, 0.85, -4.8));
  ec.add(box(0.03, 1.55, 0.85, M.dark, -inner + 0.70, 0.85, -4.8));
  ec.add(box(0.02, 0.02, 0.02, M.ledG, -inner + 0.72, 1.5, -5.1));
  ec.add(box(0.02, 0.02, 0.02, M.ledA, -inner + 0.72, 1.5, -4.5));
  ec.add(box(0.02, 0.02, 0.02, M.ledG, -inner + 0.72, 1.38, -4.8));
  group.add(ec);

  // 8) 料箱堆（-X/+Z 角落）
  const crates = new THREE.Group(); crates.name = "lab-crates";
  crates.add(box(0.7, 0.45, 0.7, M.crate, -inner + 0.42, 0.225, inner - 0.42));
  crates.add(box(0.62, 0.4, 0.62, M.crate, -inner + 0.40, 0.65, inner - 0.45));
  crates.add(box(0.56, 0.36, 0.56, M.crate, -inner + 0.44, 1.03, inner - 0.40));
  crates.add(box(0.7, 0.45, 0.7, M.crate, -inner + 1.30, 0.225, inner - 0.40));
  group.add(crates);

  // 9) 木托盘 + 工件箱（-Z 墙，另一侧）
  const pallets = new THREE.Group(); pallets.name = "lab-pallets";
  pallets.add(box(1.2, 0.14, 1.0, M.wood, 5.6, 0.07, -inner + 0.62));
  pallets.add(box(1.0, 0.7, 0.8, M.crate, 5.6, 0.49, -inner + 0.62));
  pallets.add(box(1.0, 0.1, 0.8, M.dark, 5.6, 0.88, -inner + 0.62));
  pallets.add(box(1.2, 0.14, 1.0, M.wood, 5.6, 0.07, -inner + 1.85));
  pallets.add(box(0.9, 0.55, 0.75, M.crate, 5.6, 0.415, -inner + 1.85));
  group.add(pallets);

  // 10) 工具车（+Z 墙前，不挡中心）
  const cart = new THREE.Group(); cart.name = "lab-cart";
  cart.add(box(0.7, 0.06, 1.0, M.dark, 6.0, 0.85, inner - 1.2));
  cart.add(box(0.62, 0.72, 0.9, M.cab, 6.0, 0.46, inner - 1.2));
  for (const [dx, dz] of [[-0.28, -0.42], [0.28, -0.42], [-0.28, 0.42], [0.28, 0.42]]) {
    cart.add(box(0.09, 0.1, 0.09, M.skirt, 6.0 + dx, 0.05, inner - 1.2 + dz));
  }
  group.add(cart);

  // ---------------- 灯光 ----------------
  const lights = [];
  for (const sz of [-3.6, 0, 3.6]) {
    const pl = new THREE.PointLight(0xf4f7ff, 0.32, 12, 2);
    pl.position.set(0, H - 0.25, sz);
    group.add(pl); lights.push(pl);
  }

  /**
   * 智能剖切：每帧按相机位置决定"哪面墙挡视线"并平滑淡出。
   * @param {THREE.Camera} cam
   * @param {number} dt 帧间隔(s)，缺省按 60fps
   */
  function updateVisibility(cam, dt = 1 / 60) {
    if (!cam) return;
    const p = cam.position;
    const k = 1 - Math.exp(-7 * Math.min(Math.max(dt, 0), 0.1));
    for (const e of shells) {
      const target = e.test(p) ? WALL_FADE_OPACITY : 1;
      e.op += (target - e.op) * k;
      if (Math.abs(e.op - target) < 1e-3) e.op = target;
      for (const m of e.mats) m.opacity = e.op;
    }
  }

  group.traverse((o) => { if (o.geometry && !geos.includes(o.geometry)) geos.push(o.geometry); });
  function dispose() {
    geos.forEach((g) => g.dispose());
    Object.values(M).forEach((m) => m.dispose());
    cloneMats.forEach((m) => m.dispose());
    lights.length = 0;
    shells.length = 0;
  }

  return { group, updateVisibility, dispose };
}
