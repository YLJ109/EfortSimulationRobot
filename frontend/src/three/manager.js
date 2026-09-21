// =====================================================================
// Three.js 单例管理器 —— 全局单 WebGLRenderer + **单场景 + 单套模型**。
//
// 设计（对应"三视图统一模板"）：
//   - 只有 1 个 Scene / Camera / OrbitControls，只有 1 份程序化模型 + 1 份官方数模。
//   - 切视图 = 把同一张 canvas 在三个容器之间搬移，场景与模型原地不动。
//   - 官方模型直接用 GLB **原件**构建（不 clone），Object3D.attach() 的源父级
//     天然是 GLB 根节点（rotX(-90°)+scale(0.001)），彻底消灭"第二份模型放大
//     1000 倍"这类 bug —— 份数只有 1，就没有"哪一份写错了"的问题。
// =====================================================================
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { buildRobot } from "../robot/robotModel.js";
import { FALLBACK_DH } from "../config.js";
import { buildCell, PLATFORM_H, PLATFORM_TOP_SIZE } from "./cell.js";
import { buildLab } from "./lab.js";
import { SafetyFence } from "./safety.js";

// ---- 单例资源 ----
let renderer = null;
let scene = null;
let camera = null;
let controls = null;
let envTex = null;

const containers = {};   // view -> HTMLElement（canvas 的落点）
let activeView = "live";

let robot = null;        // 程序化模型（兜底） ×1
let official = null;     // 官方数模（可动） ×1
let fence = null;        // 安全围栏
let cell = null;         // 地板 + 网格 + 阴影 + 地标线
let lab = null;          // 实验室房间(14m)外壳 + 道具
let showGlb = true;      // 默认展示官方真机数模
let activeDh = null;
let activeMounting = "floor";

const deg2rad = (d) => (d * Math.PI) / 180;

// GBK 名称还原: GLTFLoader 把 GBK 字节按 latin1 读出(零件名显示为乱码),
// 这里转回字节再用 gbk 解码, 以便按"转座/大臂/手腕体/手腕/法兰"精确归类连杆。
function fixGbkName(s) {
  if (!s) return "";
  try {
    const bytes = Uint8Array.from(s, (ch) => ch.charCodeAt(0) & 0xff);
    return new TextDecoder("gbk").decode(bytes);
  } catch (e) {
    return s;
  }
}

// 官方数模零件名 -> 连杆号 (0=固定底座, 1~6=J1~J6 所属连杆)
const NAME_TO_LINK = { "转座": 1, "大臂": 2, "手腕体": 4, "手腕": 5, "法兰": 6 };

// ---------------------------------------------------------------------
// 官方数模配色（对照现场真机照片：白色漆面机身 + 深灰五金 + 铝银法兰）
// ---------------------------------------------------------------------
const SKIN_BODY = 0xe9ecef;
const SKIN_JOINT = 0x4a5057;
const SKIN_HARD = 0x23272c;
const SKIN_METAL = 0xc2c8d0;
const SKIN_LOGO = "#c8232c";

function skinMat(color, metalness, roughness) {
  return new THREE.MeshStandardMaterial({ color, metalness, roughness });
}

function skinOf(p) {
  if (p.name === "法兰") return skinMat(SKIN_METAL, 0.80, 0.28);
  if (p.maxDim >= 200) return skinMat(SKIN_BODY, 0.15, 0.42);
  if (p.maxDim >= 60) return skinMat(SKIN_JOINT, 0.45, 0.38);
  return skinMat(SKIN_HARD, 0.55, 0.40);
}

let logoTex = null;

/** 生成 EFORT 红色标识贴图（透明底，只用 canvas，不依赖外部素材）。 */
function makeLogoTexture() {
  if (logoTex) return logoTex;
  const c = document.createElement("canvas");
  c.width = 512; c.height = 160;
  const g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = SKIN_LOGO;
  g.font = "italic 900 116px Arial, Helvetica, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText("EFORT", c.width / 2, c.height / 2 + 4);
  logoTex = new THREE.CanvasTexture(c);
  logoTex.colorSpace = THREE.SRGBColorSpace;
  logoTex.anisotropy = 4;
  return logoTex;
}

/**
 * 小臂（J4 连杆 / 手腕体）红色 EFORT 标识，贴在 **±Y 两侧面（上/下面）**。
 *
 * 位置**不写死**：由 J4 连杆零件的几何包围盒实测得出（单位 mm / 基座坐标系）：
 *   - 贴在 ±Y 面（上/下两面），平面沿前臂长度方向(X)铺开，中心取
 *     (cx, ±(半高 + 1.5), cz)，cx 为连杆 X 中点(略偏右)、cz 为 Z(深度) 中点。
 *   - 两面各自绕 X 转 ±90° 朝外（上面 -90° / 下面 +90°），文字沿前臂读向一致、不镜像。
 *   - 标识长宽按 bbox 自适应并夹在 [80,200] × [26,48] mm，保证比例协调。
 */
function buildArmLogo(width, height, cx, cz, yTop, yBot) {
  const grp = new THREE.Group();
  grp.name = "arm-logo";
  const mat = new THREE.MeshStandardMaterial({
    map: makeLogoTexture(), transparent: true, roughness: 0.42, metalness: 0.0,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  });
  const geo = new THREE.PlaneGeometry(width, height);
  [-1, 1].forEach((s) => {
    const m = new THREE.Mesh(geo, mat);
    // s>0 → 上面(+Y，绕 X 转 -90° 使法线朝 +Y)；s<0 → 下面(-Y，绕 X 转 +90°)
    m.position.set(cx, s > 0 ? yTop : yBot, cz);
    m.rotation.set(s > 0 ? -Math.PI / 2 : Math.PI / 2, 0, 0);
    grp.add(m);
  });
  return grp;
}

// ---------------------------------------------------------------------
// 末端执行器：J6 法兰上的气动平行夹爪（按现场照片复刻）
// ---------------------------------------------------------------------
const EE_FACE = 8;
const EE_SCALE = 2;
const EE_OPEN0 = 46;

function buildEndEffector() {
  const outer = new THREE.Group();
  outer.name = "end-effector";
  const g = new THREE.Group();
  g.scale.setScalar(EE_SCALE);
  outer.add(g);

  // 建模坐标下的夹指半开度 = 真实开度 / (2 × 放大倍数)；下限 30mm 防穿插
  const open2model = (mm) => Math.max(30, Math.min(mm, 160)) / 2 / EE_SCALE;

  const plate = new THREE.Mesh(new THREE.CylinderGeometry(30, 30, 10, 36), skinMat(SKIN_METAL, 0.8, 0.28));
  plate.rotation.x = Math.PI / 2;
  plate.position.z = EE_FACE + 5;
  g.add(plate);
  for (let k = 0; k < 6; k++) {
    const a = (k / 6) * Math.PI * 2;
    const b = new THREE.Mesh(new THREE.CylinderGeometry(3.5, 3.5, 6, 12), skinMat(SKIN_HARD, 0.85, 0.3));
    b.rotation.x = Math.PI / 2;
    b.position.set(Math.cos(a) * 23, Math.sin(a) * 23, EE_FACE + 11);
    g.add(b);
  }

  const bodyZ0 = EE_FACE + 10;
  const body = new THREE.Mesh(new THREE.BoxGeometry(52, 58, 46), skinMat(0x33383f, 0.5, 0.38));
  body.position.z = bodyZ0 + 23;
  g.add(body);
  const face = new THREE.Mesh(new THREE.BoxGeometry(46, 52, 6), skinMat(SKIN_METAL, 0.72, 0.3));
  face.position.z = bodyZ0 + 46 + 3;
  g.add(face);

  const fingerZ = bodyZ0 + 46 + 6 + 26;
  const fingers = [];
  [-1, 1].forEach((s) => {
    const f = new THREE.Mesh(new THREE.BoxGeometry(40, 14, 52), skinMat(0xc9cfd6, 0.7, 0.32));
    f.position.set(0, s * open2model(EE_OPEN0), fingerZ);
    g.add(f);
    fingers.push({ mesh: f, sign: s });
  });

  [-1, 1].forEach((s) => {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(s * 10, s * 26, bodyZ0 + 26),
      new THREE.Vector3(s * 30, s * 46, EE_FACE + 10),
      new THREE.Vector3(s * 44, s * 50, EE_FACE - 40),
      new THREE.Vector3(s * 42, s * 52, EE_FACE - 110),
    ]);
    g.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 24, 4, 8, false), skinMat(0x2f6fd0, 0.15, 0.55)));
  });

  [-1, 1].forEach((s) => {
    const port = new THREE.Mesh(new THREE.CylinderGeometry(5, 5, 10, 12), skinMat(0x9aa2ab, 0.85, 0.3));
    port.rotation.z = Math.PI / 2;
    port.position.set(0, s * 30, bodyZ0 + 30);
    g.add(port);
  });

  function setOpen(mm) {
    const half = open2model(mm);
    fingers.forEach((f) => { f.mesh.position.y = f.sign * half; });
  }

  return { group: outer, setOpen };
}

function ensureRenderer() {
  if (renderer) return renderer;
  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.domElement.style.display = "block";
  renderer.domElement.style.width = "100%";
  renderer.domElement.style.height = "100%";
  renderer.domElement.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    console.warn("WebGL context lost — 请刷新页面 (F5)");
  });
  return renderer;
}

/** 懒创建唯一场景（不依赖容器，容器只用于 canvas 落点）。 */
function ensureScene() {
  if (scene) return scene;
  const r = ensureRenderer();
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x141920);
  scene.fog = new THREE.Fog(0x141920, 9, 24);   // 大房间远处柔和收进背景

  camera = new THREE.PerspectiveCamera(50, 1, 0.01, 120);
  camera.position.set(1.7, 1.35, 1.75);

  controls = new OrbitControls(camera, r.domElement);
  controls.target.set(0, 0.45, 0);
  controls.minDistance = 0.6;
  controls.maxDistance = 9;          // 房间 14m，取景可拉远仍不穿墙
  controls.enableDamping = true;
  controls.enabled = true;

  if (!envTex) {
    try {
      const pmrem = new THREE.PMREMGenerator(r);
      envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
      pmrem.dispose();
    } catch (e) {
      console.warn("环境贴图生成失败, 用纯灯光:", e);
    }
  }
  if (envTex) {
    scene.environment = envTex;
    scene.environmentIntensity = 0.65;
  }
  scene.add(new THREE.HemisphereLight(0xffffff, 0x2a323c, 0.85));
  const dir = new THREE.DirectionalLight(0xfff6e8, 0.95);   // 略暖的主光
  dir.position.set(1.5, 2.5, 1.0);
  scene.add(dir);
  const fill = new THREE.DirectionalLight(0xdde8ff, 0.3);   // 略冷的补光（让金属/地坪有层次）
  fill.position.set(-2.0, 1.8, -1.5);
  scene.add(fill);

  cell = buildCell();
  scene.add(cell.group);

  lab = buildLab();
  scene.add(lab.group);

  fence = new SafetyFence(scene);
  // 机器人站在站台上 → 报警垫改铺到**台面**上（否则会被站台本体整块盖住）。
  // 落地安装才铺台面；吊装/侧装保持铺地坪。
  if (baseLiftY() > 0) {
    fence.setPadPlane({ y: PLATFORM_H + 0.004, size: PLATFORM_TOP_SIZE });
  }
  return scene;
}

// ---- 安全状态广播 ----
let safetyCb = null;
let lastSafetyKey = "";

export function onSafety(cb) { safetyCb = cb; }

/**
 * 热更新围栏配置（不需要重建场景）。
 * @param {object} cfg 配置
 * @param {boolean} applyCam 是否同时把相机移到配置里的视角。
 *   只在启动时/点「复位视角」时传 true —— 否则每拖一次滑块视角就被拽回去。
 */
export function setSafetyConfig(cfg, applyCam = false) {
  ensureScene();
  fence.applyConfig(cfg);
  if (applyCam && cfg && cfg.camera) {
    applyCameraPose(cfg.camera.position, cfg.camera.target);
  }
}

/** 当前生效的机器人根节点（围栏包围盒与可见性都用它）。 */
export function getActiveRoot() {
  if (showGlb && official) return official.root;
  return robot ? robot.root : null;
}

/**
 * 地面碰撞检测对象：J1 连杆子树（不含固定底座）。
 * 底座本来就贴在地面上（min.y≈0），整模型取包围盒会永远"碰撞地面"，
 * 所以只取 J1 及以上部分 —— 它的最低点就是真正可能撞地的部位（小臂/腕/夹爪）。
 */
export function getGroundRoot() {
  const m = showGlb && official ? official : robot;
  return m && m.joints && m.joints[0] ? m.joints[0] : null;
}

/**
 * 机器人立足面高度(m)：落地安装时站在 cell 的**站台台面**上(PLATFORM_H)，
 * 吊装/侧装不抬。地面碰撞检测的"零距离参考面"与模型抬升量必须取同一个值。
 */
function baseLiftY() {
  return (activeMounting === "ceiling" || activeMounting === "wall") ? 0 : PLATFORM_H;
}

function updateSafety(tSec) {
  if (!fence) return;
  const root = getActiveRoot();
  // refFloorY = 台面高度：站在站台上时，"离地 0mm" 指台面而非房间地坪。
  const r = fence.update(root, getGroundRoot(), tSec, baseLiftY());
  const key = `${r.state}|${r.zoneId}|${r.clearance.toFixed(3)}`;
  if (key !== lastSafetyKey) {
    lastSafetyKey = key;
    if (safetyCb) safetyCb(r);
  }
}

/** 登记一个视图的 3D 容器（各组件 onMounted 调用）。同一个 scene，不新建。 */
export function registerView(view, container) {
  ensureScene();
  containers[view] = container;
  if (activeView === view) switchView(view);
  return container;
}

/** 构建程序化机器人模型（只建一份），dh 已兜底。 */
export function initRobots(dh, mounting = "floor") {
  ensureScene();
  const dhOk = dh && dh.joints && dh.joints.length === 6 ? dh : FALLBACK_DH;
  activeDh = dhOk;
  activeMounting = mounting || "floor";
  if (!robot) {
    robot = buildRobot(dhOk, activeMounting);
    robot.root.position.y = baseLiftY();   // 站到站台台面上
    scene.add(robot.root);
  }
  applyAppearance();
}

// ---------------------------------------------------------------------
// 姿态平滑：真机数据 20Hz、键盘/滑块是跳变，直接给模型会出现"一顿一顿"。
// 这里维护 target → current 的一阶指数收敛，每帧以 60fps 逼近，动作就丝滑了。
// 收敛速率 k=14 /s：约 200ms 到位，肉眼跟手但不抖动。
// ---------------------------------------------------------------------
const SMOOTH_K = 14;
const qTarget = [0, 0, 0, 0, 0, 0];
const qCurrent = [0, 0, 0, 0, 0, 0];
let qReady = false;
let lastFrameMs = 0;

function pushPose(q) {
  if (robot) robot.applyJoints(q);
  if (official) official.applyJoints(q);
}

/**
 * 应用关节角(度) —— 程序化与官方两套同步驱动（只有一份，切外观不丢姿态）。
 * 只更新"目标姿态"，实际模型由渲染循环逐帧平滑逼近。
 */
export function applyRobotPose(qDeg) {
  for (let i = 0; i < 6; i++) qTarget[i] = qDeg && qDeg[i] ? qDeg[i] : 0;
  if (!qReady) {
    for (let i = 0; i < 6; i++) qCurrent[i] = qTarget[i];
    qReady = true;
    pushPose(qCurrent);
  }
}

/** 每帧推进平滑（返回是否有位移）。 */
function stepSmoothing(dt) {
  if (!qReady) return false;
  const k = 1 - Math.exp(-dt * SMOOTH_K);
  let moved = false;
  for (let i = 0; i < 6; i++) {
    const d = qTarget[i] - qCurrent[i];
    if (Math.abs(d) < 1e-4) { qCurrent[i] = qTarget[i]; continue; }
    qCurrent[i] += d * k;
    moved = true;
  }
  if (moved) pushPose(qCurrent);
  return moved;
}

/**
 * 读取当前生效模型的末端 TCP（世界坐标 mm）。
 * ★ 用**目标姿态**计算：平滑动画只是视觉过渡，IK/坐标显示必须拿到真实指令值，
 *   否则连续点动会累积滞后误差。算完立刻把动画姿态还原。
 */
export function getTcp() {
  const r = showGlb && official ? official : robot;
  if (!r || !r.getTcp) return { x: 0, y: 0, z: 0 };
  pushPose(qTarget);
  const t = r.getTcp();
  pushPose(qCurrent);
  return t;
}

/** 切换活动视图：把 canvas 搬到该视图容器 + resize。场景与模型不动。 */
export function switchView(view) {
  ensureScene();
  activeView = view;
  const el = containers[view];
  if (!el) return;
  if (renderer.domElement.parentElement !== el) el.appendChild(renderer.domElement);
  resizeView();
}

export function resizeView() {
  ensureScene();
  const el = containers[activeView];
  if (!el) return;
  const w = el.clientWidth, h = el.clientHeight;
  if (w === 0 || h === 0) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

export function renderFrame() {
  ensureScene();
  const now = performance.now();
  const dt = lastFrameMs ? Math.min(0.1, (now - lastFrameMs) / 1000) : 1 / 60;
  lastFrameMs = now;
  stepSmoothing(dt);                 // 姿态平滑逼近 → 动作丝滑
  controls.update();
  if (lab) lab.updateVisibility(camera, dt);   // 智能剖切：挡视线的墙/天花板自动淡出
  updateSafety(now / 1000);
  renderer.render(scene, camera);
}

/**
 * 构建"官方数模可动版"：把官方 GLB 的每个实体零件按 DH 关节轴挂到运动链上。
 *
 * 前提（已逐项核对）：
 *  1. GLB 由官方 STEP（AP214 装配体）转换而来，根节点带 rotX(-90°)+scale(0.001)，
 *     内部子零件顶点就是机器人基座坐标系（Z 向上, mm），与 DH 零位一致。
 *  2. 与 robotModel.buildRobot 使用同一条 DH 链，关节绕各自局部 Z 旋转。
 *  3. Object3D.attach() 自动补偿世界变换，模型外观零偏移但从此随关节运动。
 *     ★ 零件必须仍挂在 GLB 原始父级下（即传原件、不 clone 单 mesh），
 *       attach 的"源父级矩阵"才包含 mm→m 的 scale(0.001)。
 */
function buildArticulatedOfficial(parts, dh, mounting) {
  const J = dh.joints;
  const d1 = Math.abs(J[0].d), a1 = Math.abs(J[0].a);
  const a2 = Math.abs(J[1].a), a3 = Math.abs(J[2].a);
  const d4 = Math.abs(J[3].d), d6 = Math.abs(J[5].d);
  const zJ2 = d1;
  const zJ3 = d1 + a2;
  const zJ4 = zJ3 + a3;
  const xJ4 = a1;
  const xJ5 = a1 + d4;
  const xJ6 = xJ5 + d6;

  const info = parts.map((m) => {
    m.geometry.computeBoundingBox();
    const bb = m.geometry.boundingBox;
    return {
      mesh: m,
      name: fixGbkName((m.name || "").split("^")[0]),
      c: bb.getCenter(new THREE.Vector3()),
      s: bb.getSize(new THREE.Vector3()),
      maxZ: bb.max.z,
      maxDim: Math.max(bb.max.x - bb.min.x, bb.max.y - bb.min.y, bb.max.z - bb.min.z),
      bmin: bb.min.clone(),   // 基座坐标系 mm（用于实测连杆外廓，如 EFORT 标识定位）
      bmax: bb.max.clone(),
    };
  });

  function classify(p) {
    if (p.maxDim > 900) return -1;
    const byName = NAME_TO_LINK[p.name];
    if (byName !== undefined) return byName;
    if (p.maxZ <= zJ2 * 0.70) return 0;
    if (p.maxZ <= zJ2 * 1.25) return 1;
    if (p.c.x >= xJ5 - 20) return p.c.x >= xJ5 + 40 ? 6 : 5;
    if (p.c.z < zJ3) return 2;
    if (p.c.z < zJ4 && p.c.x < xJ4 + 120) return 3;
    return 4;
  }

  const root = new THREE.Group();
  root.name = "official-articulated";
  root.rotation.x = -Math.PI / 2;
  if (mounting === "ceiling") root.rotation.z = Math.PI;
  else if (mounting === "wall") root.rotation.z = Math.PI / 2;
  root.scale.setScalar(0.001);
  // ★★ 抬升（root.position.y）**绝不能在这里设**：Object3D.attach() 的语义是
  //    "保持对象世界变换不变"，它会把 root 当时的 matrixWorld 一并补偿进零件的局部矩阵。
  //    提前抬升 → 补偿里多出 −PLATFORM_H → 抬升被完全抵消，模型实际仍停在 y=0
  //    （下沉进 180mm 高的站台里，看上去就是"基座穿插 / 散架"），
  //    而 root.position.y 读起来却是"已经抬好了"，极难排查。
  //    正确做法：所有 attach() 做完之后**再**设 root.position.y（见本函数末尾）。

  const joints = [];
  let parent = root;
  for (let i = 0; i < J.length; i++) {
    const j = J[i];
    const jg = new THREE.Group(); jg.name = "J" + (i + 1);
    parent.add(jg);
    const dz = new THREE.Group(); dz.position.z = j.d; jg.add(dz);
    const ax = new THREE.Group(); ax.position.x = j.a; dz.add(ax);
    const rx = new THREE.Group(); rx.rotation.x = deg2rad(j.alpha); ax.add(rx);
    joints.push(jg);
    parent = rx;
  }
  const tcpNode = new THREE.Group();
  tcpNode.name = "TCP";
  parent.add(tcpNode);

  const ee = buildEndEffector();
  tcpNode.add(ee.group);

  function applyJoints(qDeg) {
    for (let i = 0; i < joints.length; i++) {
      const off = (J[i] && J[i].theta_offset) || 0;
      joints[i].rotation.z = deg2rad((qDeg && qDeg[i] ? qDeg[i] : 0) + off);
    }
  }

  scene.add(root);
  root.visible = false;
  applyJoints([0, 0, 0, 0, 0, 0]);
  root.updateMatrixWorld(true);

  // nodes[0]=root(底座)  nodes[1..6]=J1..J6 连杆
  const nodes = [root, joints[0], joints[1], joints[2], joints[3], joints[4], joints[5]];
  const report = [];
  const linkBox = {};      // 连杆号 -> {min, max}（基座坐标 mm），由零件几何实测
  info.forEach((p) => {
    const k = classify(p);
    if (k < 0) {
      p.mesh.removeFromParent();
      p.mesh.visible = false;
      report.push(`${p.name || "(无名)"}: 参考件→隐藏`);
      return;
    }
    p.mesh.material = skinOf(p);
    nodes[k].attach(p.mesh);
    const b = linkBox[k] || (linkBox[k] = { min: p.bmin.clone(), max: p.bmax.clone() });
    b.min.min(p.bmin);
    b.max.max(p.bmax);
    report.push(`${p.name || "(无名)"}: L${k}`);
  });

  // EFORT 标识 → 挂在 **J4 连杆（小臂/手腕体）** 的 ±Y 两侧面(上/下)，位置由该连杆实测包围盒算出
  const L4 = linkBox[4];
  if (L4) {
    const sx = L4.max.x - L4.min.x;
    const syH = L4.max.y - L4.min.y;
    // X 略偏右(+X, 朝腕部方向), 不再死居中; 偏移夹在 [半个字宽, 30% 连杆长] 上限 60mm
    const cx = (L4.min.x + L4.max.x) / 2 + Math.min(sx * 0.3, 60);   // X 偏右
    const cz = (L4.min.z + L4.max.z) / 2;   // Z(深度) 中点 → 文字沿前臂铺在中央
    const w = Math.min(200, Math.max(80, sx * 0.5));
    const h = Math.min(48, Math.max(26, syH * 0.45));
    const yTop = L4.max.y + 1.5;            // 略浮出 +Y 上面
    const yBot = L4.min.y - 1.5;            // 略浮出 -Y 下面
    const logo = buildArmLogo(w, h, cx, cz, yTop, yBot);
    root.add(logo);
    root.updateMatrixWorld(true);
    nodes[4].attach(logo);    // J4 连杆 = 小臂/手腕体
  } else {
    console.warn("[官方数模] 未找到 J4 连杆零件，跳过 EFORT 标识");
  }

  // ★★ 抬升放在**所有 attach() 之后**：此刻零件的世界坐标已被 attach 固定成 GLB 原始坐标
  //   （底面 y=0），再改 root.position.y 才能把整机一起平移到站台台面 —— 包括链条上的
  //   末端夹爪（tcpNode 子节点）与已 attach 的 EFORT 标识，全部同步上抬。
  //   落地安装抬 PLATFORM_H；吊装/侧装不抬（整体已旋转，贴装面本来就在 0）。
  if (mounting !== "ceiling" && mounting !== "wall") root.position.y = PLATFORM_H;
  root.updateMatrixWorld(true);

  // ---- 装配自检 ----
  // 只量**官方零件**的合并包围盒（不含末端夹爪/EFORT 标识，避免干扰判据）。
  // 官方数模实测：高 0.810 / 前后 0.688 / 左右 0.256 m，底面 z=0（落地时世界 y = 台面）。
  // ★ 校验不过就**抛错并把自己从场景摘掉** → loadOfficialModel 的 catch 回退程序化模型。
  //   直接把挂错的模型显示出来 = 用户看到"散架"，而且比回退更难排查。
  root.updateMatrixWorld(true);
  const pb = new THREE.Box3();
  let pbCount = 0;
  info.forEach((p) => {
    if (!p.mesh.parent) return;              // 参考件已摘除
    pb.union(new THREE.Box3().setFromObject(p.mesh));
    pbCount += 1;
  });
  const ph = pb.max.y - pb.min.y;
  const px = pb.max.x - pb.min.x;
  const pz = pb.max.z - pb.min.z;
  // 吊装/侧装整体被旋转，长宽高互换 → 判据不适用，只做落地安装的校验。
  if (mounting !== "ceiling" && mounting !== "wall") {
    const bad = [];
    if (ph < 0.70 || ph > 0.92) bad.push(`高 ${ph.toFixed(3)}m(应≈0.810)`);
    if (px < 0.55 || px > 0.85) bad.push(`前后跨度 ${px.toFixed(3)}m(应≈0.688)`);
    if (pz < 0.15 || pz > 0.40) bad.push(`左右跨度 ${pz.toFixed(3)}m(应≈0.256)`);
    if (Math.abs(pb.min.y - PLATFORM_H) > 0.03) {
      bad.push(`底面 y ${pb.min.y.toFixed(3)}(应≈台面 ${PLATFORM_H})`);
    }
    if (bad.length) {
      scene.remove(root);      // 摘掉半成品，别留在场景里当"散架"的模型
      throw new Error(`[官方数模] 装配自检失败 → 回退程序化模型：${bad.join("；")}。零件归类 ${report.join(" | ")}`);
    }
  }

  console.info(
    "[官方数模] 已按 DH 关节轴重挂 %d 个零件(高 %.3fm/前后 %.3fm/左右 %.3fm) → %s",
    pbCount, ph, px, pz, report.join(" | "),
  );

  function getTcp() {
    root.updateMatrixWorld(true);
    const v = new THREE.Vector3();
    tcpNode.getWorldPosition(v);
    return { x: v.x * 1000, y: v.y * 1000, z: v.z * 1000 };
  }

  return { root, applyJoints, getTcp, joints, setGripper: ee.setOpen };
}

/** 官方数模（GLB）→ 六轴可动版。**直接用原件，不 clone**（份数=1）。 */
export async function loadOfficialModel(url = "/models/robot_full.glb") {
  const dh = activeDh || FALLBACK_DH;
  const loader = new GLTFLoader();
  try {
    const gltf = await loader.loadAsync(url);
    gltf.scene.updateMatrixWorld(true);
    const src = gltf.scene.getObjectByName("ER8-700H") || gltf.scene.children[0] || gltf.scene;
    const parts = [];
    src.traverse((o) => { if (o.isMesh && o.geometry) parts.push(o); });
    if (!parts.length) throw new Error("GLB 中没有可用的网格零件");
    // 幂等：重复调用时先移除旧模型，避免场景里残留第二台机器人
    if (official) {
      scene.remove(official.root);
      official = null;
    }
    official = buildArticulatedOfficial(parts, dh, activeMounting);
  } catch (e) {
    console.warn("官方数模加载失败, 回退程序化模型:", e);
    official = null;
  }
  applyAppearance();
}

export function setShowGlb(v) {
  showGlb = v;
  applyAppearance();
}

export function getShowGlb() { return showGlb; }

/** 设置 J6 末端夹爪开度(mm)。 */
export function setGripperOpen(mm) {
  if (official && official.setGripper) official.setGripper(mm);
}

function applyAppearance() {
  const officialOn = !!(showGlb && official);
  if (official) official.root.visible = officialOn;
  if (robot) robot.root.visible = !officialOn;
  pushPose(qCurrent);   // 换外观后立刻把当前姿态补上，避免"切换瞬间姿态丢失"
}

// ---------------- 相机视角（保存 / 复位） ----------------
export function getCameraPose() {
  ensureScene();
  return {
    position: [camera.position.x, camera.position.y, camera.position.z],
    target: [controls.target.x, controls.target.y, controls.target.z],
  };
}

export function applyCameraPose(position, target) {
  ensureScene();
  if (Array.isArray(position) && position.length === 3) camera.position.set(...position);
  if (Array.isArray(target) && target.length === 3) controls.target.set(...target);
  controls.update();
}
