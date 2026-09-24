// =====================================================================
// 模拟摆放物体（3D 仿真沙盒）· 迭代二
// ---------------------------------------------------------------------
// 纯前端模拟，不接真机、不动真实机器人、不接相机。
//   · 源盒(3×3)：9 个物体，3红3绿3蓝，初始全黑（深烟灰可见）
//   · 目标盒：空托盘，第1排红/第2排绿/第3排蓝承接
//   · 源盒 / 目标盒 / 摄像头全部放在【站台台面】上（baseY = PLATFORM_H）
//   · 盒宽可调：整体缩放盒体与格子，源/目标物体跟随重排
//   · 落点吸合：放地面吸地、放站台吸台顶、压到物体就叠到它顶上、AABB 防穿透
//   · 卡片拖放：从左侧卡片拖物体/颜色块到 3D 投放（move / new）
//   · 吸气吸附携带、放气放下、识别显色（原语，机器人操作单独拆出去）
//
// 只关心 Three 世界（坐标单位 m），数据变更经 this.onChange 通知外部。
// =====================================================================
import * as THREE from "three";

// ---- 默认可调常量（构造时可按需覆盖） ----
export const SRC_CENTER = [-0.55, 0.58];   // 源盒中心（x, z），在站台台面、机器身前左
export const DST_CENTER = [0.55, 0.58];    // 目标盒中心（x, z），机器身前右
export const OBJ_SIZE = 0.10;              // 物体默认边长(m)——调小，避免"9个物体太高"
export const CELL = 0.16;                  // 盒内格子间距
export const GRID = 3;                     // 盒格子数 3×3
export const PICK_R = 0.14;                // 吸盘吸附半径(m)
export const BOX_OUTER = GRID * CELL + 0.04; // 盒体外沿（长/宽）
const STORE_KEY = "sim-sandbox-v1";          // 摆放状态持久化键（刷新不还原）
const WALL_H = 0.05;
const WALL_T = 0.018;
const TRAY_FLOOR_H = 0.012;                // 盒底厚度

export function makeInspectDefaults(baseY) {
  return {
    point: new THREE.Vector3(0, baseY + 0.06, 0.8),      // 镜头视点：略高于台面、低位仰视
    look: new THREE.Vector3(SRC_CENTER[0], baseY + 0.55, SRC_CENTER[1]),
  };
}

const COLOR_TEXT = { red: "红色", green: "绿色", blue: "蓝色" };
export const COLORS = ["red", "green", "blue"];
// 源盒按排给真色：第1排红 / 第2排绿 / 第3排蓝（也即目标盒摆放顺序）
const ROW_COLOR = [...COLORS];

function mat(color, rough = 0.5, metal = 0.1) {
  return new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal });
}
const BLACK_MAT = mat(0x3d434d, 0.4, 0.35);   // 未识别=深烟灰金属，深色场景也能看清
const COLOR_MAT = { red: mat(0xc0392b), green: mat(0x27ae60), blue: mat(0x2e6fdc) };
const WALL_MAT = mat(0x4a5568, 0.5, 0.3);
const TRAY_FLOOR_MAT = mat(0x2b313d, 0.86, 0.05);
const GRID_LINE_MAT = new THREE.LineBasicMaterial({ color: 0x64707e, transparent: true, opacity: 0.8 });

// ---------------------------------------------------------------------
// 碰撞核心（纯函数，供验证守卫断言）
// `a`/`b` 是 {cx,cy,cz,hw,hh,hd} 的 AABB（c=中心，h*=半长）
// ---------------------------------------------------------------------
export function aabbOverlapXZ(a, b) {
  return Math.abs(a.cx - b.cx) < a.hw + b.hw && Math.abs(a.cz - b.cz) < a.hd + b.hd;
}
export function aabbOverlap(a, b) {
  return Math.abs(a.cx - b.cx) < a.hw + b.hw
    && Math.abs(a.cy - b.cy) < a.hh + b.hh
    && Math.abs(a.cz - b.cz) < a.hd + b.hd;
}
/** 沿 X/Z 把 a 从 b 推出（最短穿透轴）。原地变更 a，返回是否位移。 */
export function resolveXZ(a, b) {
  const dx = a.cx - b.cx, dz = a.cz - b.cz;
  const px = a.hw + b.hw - Math.abs(dx);
  const pz = a.hd + b.hd - Math.abs(dz);
  if (px <= 0 || pz <= 0) return false;
  if (px < pz) a.cx += dx >= 0 ? px : -px;
  else a.cz += dz >= 0 ? pz : -pz;
  return true;
}

function makeLabelSprite(text) {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 56;
  const g = c.getContext("2d");
  g.fillStyle = "rgba(14,17,22,0.78)";
  g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = "#3a4150"; g.lineWidth = 3; g.strokeRect(1.5, 1.5, c.width - 3, c.height - 3);
  g.fillStyle = "#ff7a18";
  g.font = "700 30px 'Microsoft YaHei UI', sans-serif";
  g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(text, c.width / 2, c.height / 2);
  const tex = new THREE.CanvasTexture(c);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthWrite: false }));
  sp.scale.set(0.52, 0.12, 1);
  return sp;
}

// ---------------------------------------------------------------------
export class SimObjects {
  constructor(scene, baseY = 0, platHalf = 0.82) {
    this.scene = scene;
    this.baseY = baseY;          // 站台台面顶 y
    this.platHalf = platHalf;    // 站台半宽（落点吸合的台面范围）
    this.group = new THREE.Group();
    this.group.name = "sim-sandbox";
    this._persistTimer = 0;

    this.objs = [];
    this.seq = 0;
    this.suck = false;
    this.held = null;
    this.selId = null;
    this.onChange = null;
    this.records = [];

    this.solids = [];            // 托盘壁静态 AABB
    this.trays = {};             // id -> {id, group, outer, cx, cz}
    this.traySlabs = [];
    this.drag = null;
    this.cameraGroup = null;     // 摄像头点位组（可拖拽）
    this.cameraLabel = null;
    this.camPicks = [];          // 摄像头可拾取网格

    const { point, look } = makeInspectDefaults(baseY);
    this.inspectPoint = point;
    this.inspectLook = look;
    this.outer = BOX_OUTER;

    this._buildTray("src", SRC_CENTER[0], SRC_CENTER[1], "源盒 · 供料");
    this._buildTray("dst", DST_CENTER[0], DST_CENTER[1], "目标盒 · 放置");
    this._buildSource();
    this._buildCameraPoint();

    scene.add(this.group);
    this._loadSaved();
  }

  /** 随站台参数更新承载面与范围（由 manager 在安全配置变化时调用）。 */
  setBase(baseY, platHalf) {
    this.baseY = baseY;
    this.platHalf = platHalf;
    const d = makeInspectDefaults(baseY);
    this.inspectPoint.copy(d.point);
    this.inspectLook.copy(d.look);
    if (this.cameraGroup) {
      this.cameraGroup.position.set(this.inspectPoint.x, baseY, this.inspectPoint.z);
      if (this.cameraLabel) {
        this.cameraLabel.position.set(this.inspectPoint.x, baseY + 0.3, this.inspectPoint.z - 0.3);
      }
    }
    this.resolveAll();
    this._emit();
  }

  _emit() {
    if (this.onChange) this.onChange();
    this._save();
  }

  // ------------- 状态持久化（刷新不还原） -------------
  _snapshot() {
    const cam = [this.inspectPoint.x, this.inspectPoint.y, this.inspectPoint.z];
    const look = [this.inspectLook.x, this.inspectLook.y, this.inspectLook.z];
    return {
      outer: this.outer,
      srcCenter: this.trays.src ? [this.trays.src.cx, this.trays.src.cz] : SRC_CENTER,
      dstCenter: this.trays.dst ? [this.trays.dst.cx, this.trays.dst.cz] : DST_CENTER,
      cam: { point: cam, look },
      objs: this.objs.map((o) => {
        const p = o.mesh.position;
        return {
          x: Math.round(p.x * 1000) / 1000, y: Math.round(p.y * 1000) / 1000,
          z: Math.round(p.z * 1000) / 1000, size: Math.round(o.size * 1000) / 1000,
          colorTrue: o.colorTrue, revealed: o.revealed, laid: o.laid, tray: o.tray,
        };
      }),
    };
  }

  _save() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(this._snapshot())); }
    catch (e) { /* 隐私/容量异常时静默，不影响交互 */ }
  }

  _loadSaved() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (!raw) return;
      this._applySaved(JSON.parse(raw));
    } catch (e) { /* 损坏存档忽略 */ }
  }

  _applySaved(s) {
    this.objs.forEach((o) => this.group.remove(o.holder));
    this.objs = [];
    this.seq = 0;
    this.held = null; this.suck = false; this.selId = null; this.records = [];

    this.outer = Math.min(1.0, Math.max(0.4, +s.outer || BOX_OUTER));
    const src = (Array.isArray(s.srcCenter) && s.srcCenter.length >= 2) ? s.srcCenter : SRC_CENTER;
    const dst = (Array.isArray(s.dstCenter) && s.dstCenter.length >= 2) ? s.dstCenter : DST_CENTER;
    ["src", "dst"].forEach((id) => { this._disposeTray(id); });
    this._buildTray("src", +src[0], +src[1], "源盒 · 供料");
    this._buildTray("dst", +dst[0], +dst[1], "目标盒 · 放置");

    for (const rec of s.objs || []) {
      const pos = new THREE.Vector3(+rec.x || 0, +rec.y || 0, +rec.z || 0);
      const col = COLORS.includes(rec.colorTrue) ? rec.colorTrue : COLORS[0];
      const tray = rec.tray === "dst" ? "dst" : "src";
      const o = this._addObj(pos, col, tray);
      o.laid = !!rec.laid;
      this._applyScale(o, +rec.size || OBJ_SIZE);
      o.revealed = !!rec.revealed;
      if (o.revealed) o.mesh.material = COLOR_MAT[o.colorTrue];
      o.mesh.position.x = pos.x; o.mesh.position.z = pos.z;
      this._rest(o);
    }

    if (s.cam && Array.isArray(s.cam.point) && s.cam.point.length >= 3) {
      this.inspectPoint.set(+s.cam.point[0], +s.cam.point[1], +s.cam.point[2]);
    }
    if (s.cam && Array.isArray(s.cam.look) && s.cam.look.length >= 3) {
      this.inspectLook.set(+s.cam.look[0], +s.cam.look[1], +s.cam.look[2]);
    }
    this._placeCamera();
    this.resolveAll();
    this._emit();
  }

  _placeCamera() {
    if (!this.cameraGroup) return;
    this.cameraGroup.position.set(this.inspectPoint.x, this.baseY, this.inspectPoint.z);
    if (this.cameraLabel) {
      this.cameraLabel.position.set(this.inspectPoint.x, this.baseY + 0.3, this.inspectPoint.z - 0.3);
    }
  }

  _boxOf(o) {
    const s = o.size / 2;
    const p = o.mesh.position;
    return { cx: p.x, cy: p.y, cz: p.z, hw: s, hh: s, hd: s };
  }

  _trayHalf() { return this.outer / 2; }

  _cellCenter(cx, cz, c, r) {
    const cell = (this.outer - WALL_T - 0.02) / GRID;
    return { x: cx + (c - (GRID - 1) / 2) * cell, z: cz + (r - (GRID - 1) / 2) * cell };
  }

  _buildTray(id, cx, cz, label) {
    const outer = this.outer;
    const half = outer / 2;
    const g = new THREE.Group();
    g.name = label + "-" + id;
    this.trays[id] = { id, group: g, outer, cx, cz };

    const by = this.baseY;
    const slab = new THREE.Mesh(new THREE.BoxGeometry(outer, TRAY_FLOOR_H, outer), TRAY_FLOOR_MAT);
    slab.position.set(cx, by + TRAY_FLOOR_H / 2, cz);
    slab.userData.trayId = id;
    g.add(slab);
    this.traySlabs.push(slab);

    const mkWall = (w, d, x, z) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(w, WALL_H, d), WALL_MAT);
      m.position.set(x, by + WALL_H / 2, z);
      g.add(m);
      const solid = { tr: id, cx: x, cy: by + WALL_H / 2, cz: z, hw: w / 2, hh: WALL_H / 2, hd: d / 2 };
      this.solids.push(solid);
      return solid;
    };
    mkWall(outer + WALL_T * 2, WALL_T, cx, cz - half);
    mkWall(outer + WALL_T * 2, WALL_T, cx, cz + half);
    mkWall(WALL_T, outer + WALL_T * 2, cx - half, cz);
    mkWall(WALL_T, outer + WALL_T * 2, cx + half, cz);

    const lg = new THREE.BufferGeometry();
    const pts = [];
    for (let i = 1; i < GRID; i++) {
      const off = (i - GRID / 2) * ((outer - WALL_T - 0.02) / GRID);
      pts.push(cx + off, by + TRAY_FLOOR_H + 0.002, cz - half, cx + off, by + TRAY_FLOOR_H + 0.002, cz + half);
      pts.push(cx - half, by + TRAY_FLOOR_H + 0.002, cz + off, cx + half, by + TRAY_FLOOR_H + 0.002, cz + off);
    }
    lg.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    g.add(new THREE.Line(lg, GRID_LINE_MAT));

    const lab = makeLabelSprite(label);
    lab.position.set(cx, by + 0.02, cz);
    g.add(lab);

    this.group.add(g);
  }

  _disposeTray(id) {
    const t = this.trays[id];
    if (!t) return;
    t.group.traverse((n) => { if (n.geometry) n.geometry.dispose(); });
    this.group.remove(t.group);
    this.solids = this.solids.filter((s) => s.tr !== id);
    this.traySlabs = this.traySlabs.filter((s) => s.userData.trayId !== id);
    delete this.trays[id];
  }

  /** 调整盒宽（整体外沿，0.4~1.0m），重建托盘、源盒格内物体重排并按比例缩放。 */
  setBoxWidth(w) {
    const outer = Math.min(1.0, Math.max(0.4, +w || this.outer));
    if (outer === this.outer) return;
    const oldCell = (this.outer - WALL_T - 0.02) / GRID;
    this.outer = outer;
    const newCell = (this.outer - WALL_T - 0.02) / GRID;
    const factor = Math.max(0.4, Math.min(1.3, newCell / oldCell));
    ["src", "dst"].forEach((id) => {
      const t = this.trays[id];
      const cx = t ? t.cx : SRC_CENTER[0];
      const cz = t ? t.cz : SRC_CENTER[1];
      const label = id === "src" ? "源盒 · 供料" : "目标盒 · 放置";
      this._disposeTray(id);
      this._buildTray(id, cx, cz, label);
    });
    // 源盒"仍自动"的物体（未手动摆过 laid=false）重排到新格位并按比例缩放；
    // 手动移动/改尺寸过的（laid=true）保持当前位置与大小，除非点"重置"。
    let i = 0;
    for (const o of this.objs) {
      if (o.tray === "src" && o !== this.held && !o.laid) {
        const { cx, cz } = this.trays.src;
        const c = i % GRID, r = Math.floor(i / GRID) % GRID;
        const p = this._cellCenter(cx, cz, c, r);
        o.mesh.position.x = p.x; o.mesh.position.z = p.z;
        this._applyScale(o, o.size * factor);
        i++;
      }
    }
    this.resolveAll();
    this._emit();
  }

  _applyScale(o, size) {
    const s = Math.min(0.3, Math.max(0.04, size));
    o.size = s;
    o.mesh.scale.setScalar(s / OBJ_SIZE);
    o.edge.scale.setScalar(s / OBJ_SIZE);
  }

  _addObj(pos, colorTrue, tray = null) {
    const id = ++this.seq;
    const size = OBJ_SIZE;
    const holder = new THREE.Group();
    holder.name = "sim-obj-" + id;

    const mesh = new THREE.Mesh(new THREE.BoxGeometry(size, size, size), BLACK_MAT);
    mesh.position.copy(pos);
    mesh.userData.objId = id;
    holder.add(mesh);

    const edge = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(size, size, size)),
      new THREE.LineBasicMaterial({ color: 0x2f9bff, transparent: true, opacity: 0.95 }),
    );
    edge.visible = false;
    holder.add(edge);

    this.group.add(holder);
    this.objs.push({
      id, colorTrue, revealed: false, tray, laid: false,
      size, holder, mesh, edge, colorText: COLOR_TEXT[colorTrue],
    });
    const o = this.objs[this.objs.length - 1];
    this._rest(o);
    return o;
  }

  _buildSource() {
    const cx = this.trays.src.cx, cz = this.trays.src.cz;
    for (let i = 0; i < GRID * GRID; i++) {
      const c = i % GRID, r = Math.floor(i / GRID);
      const p = this._cellCenter(cx, cz, c, r);
      this._addObj(p, ROW_COLOR[r], "src");
    }
  }

  _buildCameraPoint() {
    const g = new THREE.Group();
    g.name = "camera-point";
    // 组中心 = 台面顶（baseY）；部件都用本体局部高度，绝不重复加 baseY，
    // 否则整组会漂在台面之上（"悬空"）。
    g.position.set(this.inspectPoint.x, this.baseY, this.inspectPoint.z);
    const base = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.03, 0.2), mat(0x161b22, 0.4, 0.3));
    base.position.set(0, 0.015, 0);
    base.userData.cam = "pt";
    g.add(base);
    this.camPicks.push(base);
    const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.1, 12), mat(0x2a3140, 0.5, 0.2));
    arm.position.set(0, 0.065, 0);
    g.add(arm);
    const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.032, 0.05, 0.08, 20), mat(0x222b39, 0.3, 0.2));
    lens.rotation.x = Math.PI / 2;
    lens.position.set(0, 0.13, 0);
    g.add(lens);
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.045, 0.06, 24),
      new THREE.MeshBasicMaterial({ color: 0xff7a18, side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(0, 0.175, 0);
    g.add(ring);
    this.cameraGroup = g;
    this.group.add(g);

    const lab = makeLabelSprite("摄像头 · 低位仰视");
    lab.position.set(g.position.x, this.baseY + 0.3, g.position.z - 0.3);
    this.cameraLabel = lab;
    this.group.add(lab);
  }

  // ---------------- 落点吸合 ----------------
  /** 返回 o 应停留的表面顶 y：地面→站台→盒底→更高物体顶，取最高。 */
  _surfaceTop(o) {
    const p = o.mesh.position;
    let surf = 0;                                  // 地面
    if (this.platHalf > 0 && Math.abs(p.x) <= this.platHalf && Math.abs(p.z) <= this.platHalf) {
      surf = this.baseY;                            // 站台台面
    }
    for (const id in this.trays) {
      const t = this.trays[id];
      if (!t) continue;
      const h = t.outer / 2;
      if (Math.abs(p.x - t.cx) <= h && Math.abs(p.z - t.cz) <= h) {
        surf = Math.max(surf, this.baseY + TRAY_FLOOR_H);   // 盒底（略高于台面）
      }
    }
    for (const other of this.objs) {
      if (other.id === o.id || other === this.held) continue;
      if (aabbOverlapXZ(this._boxOf(o), this._boxOf(other))) {
        surf = Math.max(surf, other.mesh.position.y + other.size / 2);  // 压到物体头顶上去
      }
    }
    return surf;
  }

  /** 让 o 落回表面（中心 y = 表面 + 半高），并做三维防穿透。 */
  _rest(o) {
    o.mesh.position.y = this._surfaceTop(o) + o.size / 2;
    this._resolve(o);
  }

  /** 三维防穿透：托盘壁用 XZ 约束；物体之间用 3D 重叠（允许上下叠放）。 */
  _resolve(o) {
    const a = this._boxOf(o);
    for (const s of this.solids) if (resolveXZ(a, s)) break;
    for (const other of this.objs) {
      if (other.id === o.id || other === this.held) continue;
      if (aabbOverlap(a, this._boxOf(other))) resolveXZ(a, this._boxOf(other));
    }
    o.mesh.position.x = a.cx;
    o.mesh.position.z = a.cz;
  }

  // ---------------- 交互 ----------------
  attach(renderer, camera, controls) {
    this.renderer = renderer;
    this.camera = camera;
    this.controls = controls;
    this.ray = new THREE.Raycaster();
    this.groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const el = renderer.domElement;
    el.style.cursor = "default";
    el.addEventListener("pointerdown", this._onDown);
    el.addEventListener("pointermove", this._onMove);
    el.addEventListener("pointerup", this._onUp);
    el.addEventListener("pointercancel", this._onUp);
    el.addEventListener("dragover", (e) => e.preventDefault());
    el.addEventListener("drop", this._onDrop);
  }

  _ndc(e) {
    const el = this.renderer.domElement;
    const r = el.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * 2 - 1, y: -((e.clientY - r.top) / r.height) * 2 + 1 };
  }

  _pick(e) {
    const nd = this._ndc(e);
    this.ray.setFromCamera(nd, this.camera);
    const hitObj = this.ray.intersectObjects(this.objs.map((o) => o.mesh), false)[0];
    if (hitObj) {
      const o = this._byId(hitObj.object.userData.objId);
      if (o) return { t: "obj", o };
    }
    const hitSlab = this.ray.intersectObjects(this.traySlabs, false)[0];
    if (hitSlab) return { t: "tray", id: hitSlab.object.userData.trayId };
    if (this.camPicks.length) {
      const c = this.ray.intersectObjects(this.camPicks, false)[0];
      if (c) return { t: "cam" };
    }
    return null;
  }

  _byId(id) { return this.objs.find((o) => String(o.id) === String(id)); }

  /** 拖动摄像头点位：平移组 + 记录 inspectPoint/inspectLook（始终看向源盒）。 */
  _moveCamera(dx, dz) {
    if (!this.cameraGroup) return;
    this.cameraGroup.position.x += dx;
    this.cameraGroup.position.z += dz;
    this.inspectPoint.set(this.cameraGroup.position.x, this.baseY + 0.06, this.cameraGroup.position.z);
    const t = this.trays.src;
    if (t) this.inspectLook.set(t.cx, this.baseY + 0.5, t.cz);
    if (this.cameraLabel) {
      this.cameraLabel.position.set(
        this.cameraGroup.position.x,
        this.baseY + 0.3,
        this.cameraGroup.position.z - 0.3,
      );
    }
    this._emit();
  }

  _groundHit(e, v) {
    this.ray.setFromCamera(this._ndc(e), this.camera);
    return this.ray.ray.intersectPlane(this.groundPlane, v);
  }

  _onDown = (e) => {
    if (e.button !== 0) return;
    const hit = this._pick(e);
    if (hit && hit.t === "obj") {
      this.select(hit.o.id);
      this._syncSelection();
      this.drag = { type: "obj", id: hit.o.id };
    } else if (hit && hit.t === "cam") {
      // 点到摄像头 → 拖动点位
      this.select(null);
      this._syncSelection();
      const v = new THREE.Vector3();
      this._groundHit(e, v);
      this.drag = { type: "cam", lastX: v.x, lastZ: v.z };
    } else if (hit) {
      this.select(null);
      this._syncSelection();
      const v = new THREE.Vector3();
      this._groundHit(e, v);
      this.drag = { type: "tray", id: hit.id, lastX: v.x, lastZ: v.z };
    } else {
      this.select(null);
      this._syncSelection();
      return;
    }
    if (this.controls) this.controls.enabled = false;
    if (this.renderer) this.renderer.domElement.setPointerCapture(e.pointerId);
  };

  _onMove = (e) => {
    if (!this.drag) return;
    const v = new THREE.Vector3();
    if (!this._groundHit(e, v)) return;
    if (this.drag.type === "obj") {
      const o = this._byId(this.drag.id);
      if (!o || o === this.held) return;
      o.mesh.position.x = v.x;
      o.mesh.position.z = v.z;
      this._rest(o);
    } else if (this.drag.type === "cam") {
      const dx = v.x - this.drag.lastX, dz = v.z - this.drag.lastZ;
      this.drag.lastX = v.x; this.drag.lastZ = v.z;
      if (dx !== 0 || dz !== 0) this._moveCamera(dx, dz);
    } else {
      const dx = v.x - this.drag.lastX, dz = v.z - this.drag.lastZ;
      this.drag.lastX = v.x; this.drag.lastZ = v.z;
      if (dx !== 0 || dz !== 0) this._moveTray(this.drag.id, dx, dz);
    }
  };

  _onUp = () => {
    if (!this.drag) return;
    if (this.drag.type === "obj") {
      const o = this._byId(this.drag.id);
      if (o) o.laid = true;          // 手动拖过 → 标记已摆放，后续不再被盒宽重置
    }
    this.drag = null;
    if (this.controls) this.controls.enabled = true;
    this._emit();
  };

  /** 卡片拖放：move:<id> 移动既有物体；new:<color> 在落点新增。 */
  _onDrop = (e) => {
    e.preventDefault();
    const dt = (e.dataTransfer && e.dataTransfer.getData("application/x-simobj")) || "";
    const v = new THREE.Vector3();
    if (!dt || !this._groundHit(e, v)) return;
    if (dt.startsWith("move:")) {
      const o = this._byId(dt.slice(5));
      if (o && o !== this.held) {
        o.mesh.position.x = v.x; o.mesh.position.z = v.z;
        o.laid = true;
        this._rest(o);
        this.select(o.id);
      }
    } else if (dt.startsWith("new:")) {
      const color = dt.slice(4);
      if (COLORS.includes(color)) {
        const o = this._addObj(v, color, null);
        o.mesh.position.x = v.x; o.mesh.position.z = v.z;
        o.laid = true;              // 拖放到任意处 → 已摆放
        this._rest(o);
        this.select(o.id);
      }
    }
    this._emit();
  };

  _moveTray(id, dx, dz) {
    const t = this.trays[id];
    if (!t) return;
    const g = t.group;
    const cx0 = t.cx, cz0 = t.cz;
    t.cx += dx; t.cz += dz;
    g.position.x += dx; g.position.z += dz;
    const ax = t.cx - cx0, az = t.cz - cz0;
    for (const o of this.objs) {
      if (o.tray !== id || o === this.held) continue;
      o.mesh.position.x += ax; o.mesh.position.z += az;
      // 保持躺在新表面的盒底上
      o.mesh.position.y = Math.max(this.baseY + TRAY_FLOOR_H + o.size / 2, o.mesh.position.y);
      this._resolve(o);
    }
  }

  // ---------------- 对象操作 ----------------
  select(id) {
    this.selId = id;
    this.objs.forEach((o) => { o.edge.visible = o.id === id; });
  }
  _syncSelection() { this._emit(); }

  add(color) {
    const colorTrue = color || COLORS[this.objs.length % COLORS.length];
    const t = this.trays.src;
    const o = this._addObj(this._cellCenter(t.cx, t.cz, 0, 0), colorTrue, "src");
    this.resolveAll();
    this.select(o.id);
    this._emit();
    return o.id;
  }

  moveTo(id, x, z) {
    const o = this._byId(id);
    if (!o || o === this.held) return;
    o.mesh.position.x = +x;
    o.mesh.position.z = +z;
    o.laid = true;                  // 坐标放置 → 已摆放
    this._rest(o);
    this._emit();
  }

  remove(id) {
    const i = this.objs.findIndex((o) => o.id === id);
    if (i < 0) return;
    const o = this.objs[i];
    if (o === this.held) this.held = null;
    this.group.remove(o.holder);
    this.objs.splice(i, 1);
    if (this.selId === id) this.selId = null;
    this._emit();
  }

  resize(id, size) {
    const o = this._byId(id);
    if (!o) return;
    this._applyScale(o, size);
    o.laid = true;                  // 手动调尺寸 → 保持，不被盒宽缩放重置
    this._rest(o);
    this._emit();
  }

  setSuck(v) {
    this.suck = !!v;
    if (!this.suck && this.held) {
      this.held.mesh.position.y = this._surfaceTop(this.held) + this.held.size / 2;
      this.held = null;
    } else if (this.suck && !this.held) this._tryPickup(this.lastTcp);
    this._emit();
  }

  identify(idOrAll) {
    const list = idOrAll == null ? this.objs : [this._byId(idOrAll)].filter(Boolean);
    for (const o of list) {
      if (o.revealed) continue;
      o.revealed = true;
      o.mesh.material = COLOR_MAT[o.colorTrue];
      this.records.push({ seq: o.id, color: o.colorText, conf: +(0.86 + Math.random() * 0.13).toFixed(2) });
    }
    this._emit();
  }

  resolveAll() {
    for (const o of this.objs) {
      if (o === this.held) continue;
      o.mesh.position.y = this._surfaceTop(o) + o.size / 2;
      this._resolve(o);
    }
  }

  // ---------------- 每帧动态 ----------------
  tick(tcpM) {
    this.lastTcp = tcpM;
    if (this.suck) {
      if (!this.held) this._tryPickup(tcpM);
      if (this.held) {
        const m = this.held.mesh.position;
        m.x = tcpM.x; m.z = tcpM.z;
        m.y = tcpM.y - 0.02 - this.held.size / 2;
        if (m.y < this._surfaceTop(this.held) + this.held.size / 2) m.y = this._surfaceTop(this.held) + this.held.size / 2;
      }
    }
  }

  _tryPickup(tcpM) {
    let best = null, bestD = PICK_R;
    for (const o of this.objs) {
      if (o === this.held) continue;
      const p = o.mesh.position;
      const d = Math.hypot(p.x - tcpM.x, p.z - tcpM.z);
      if (d < bestD) { bestD = d; best = o; }
    }
    if (best) this.held = best;
  }

  reset() {
    // 恢复默认：清空物体、盒位、盒宽、摄像头，并清除持久化存档
    this.objs.forEach((o) => this.group.remove(o.holder));
    this.objs = [];
    this.seq = 0;
    this.held = null;
    this.suck = false;
    this.selId = null;
    this.records = [];
    try { localStorage.removeItem(STORE_KEY); } catch (e) { /* 无 localStorage 环境忽略 */ }

    this.outer = BOX_OUTER;
    ["src", "dst"].forEach((id) => this._disposeTray(id));
    this._buildTray("src", SRC_CENTER[0], SRC_CENTER[1], "源盒 · 供料");
    this._buildTray("dst", DST_CENTER[0], DST_CENTER[1], "目标盒 · 放置");

    const d = makeInspectDefaults(this.baseY);
    this.inspectPoint.copy(d.point);
    this.inspectLook.copy(d.look);
    this._placeCamera();
    this._buildSource();
    this._emit();
  }

  getSnapshot() {
    return this.objs.map((o) => {
      const p = o.mesh.position;
      return {
        id: o.id, colorText: o.colorText, revealed: o.revealed, held: o === this.held,
        size: o.size, x: p.x, z: p.z,
      };
    });
  }

  dispose() {
    if (this.renderer) {
      const el = this.renderer.domElement;
      el.removeEventListener("pointerdown", this._onDown);
      el.removeEventListener("pointermove", this._onMove);
      el.removeEventListener("pointerup", this._onUp);
      el.removeEventListener("pointercancel", this._onUp);
    }
    this.objs.forEach((o) => o.holder.traverse((n) => {
      if (n.geometry) n.geometry.dispose();
      if (n.material && n.material.map) n.material.map.dispose();
    }));
    this.scene.remove(this.group);
  }
}