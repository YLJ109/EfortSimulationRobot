<script setup>
// 模拟仿真视图：键盘控制 + 关节/直角指令预演（只算不发）。
// ★ 外观固定使用官方数模（ER8-700H GLB 可动版），本页不再提供模型切换。
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useRobotStore } from "../stores/robot.js";
import { getTcp } from "../three/manager.js";
import { apiUrl } from "../config.js";
import { dancePose } from "../utils/dance.js";
import { parseXpl } from "../utils/xplParser.js";
import MonitorLayout from "./MonitorLayout.vue";
import Icon from "./Icon.vue";

const robot = useRobotStore();

// =====================================================================
// 只读接口的响应处理（/control/preview 与 /control/ik）
//
// ★ 这两个接口是**公开只读**的（后端 control.py 的 router_ro，不挂 require_control）：
//   模拟仿真页在 tabs.js 里是公开页，让"只算不发"的预演去要控制令牌，只会让未登录
//   访客的控制台刷满 401，按钮本身也永远点不动。
// ★ 但仍必须处理失败：FastAPI 的校验错误（422）把 detail 放成数组、
//   HTTPException 把 detail 放成字符串或对象。不解析就只能显示"undefined"，
//   现场拿着这句话没法排查（这正是之前踩过的坑）。
// =====================================================================
async function readJson(r) {
  try { return await r.json(); } catch (e) { return {}; }
}

function errText(d, status) {
  const detail = d && d.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    // pydantic 校验错误：[{loc:[...], msg:"..."}] —— 只取第一条，够定位了
    const m = detail[0] && detail[0].msg;
    if (m) return String(m);
  }
  if (detail && typeof detail === "object") {
    const m = detail.message || detail.error;
    if (m) return String(m);
  }
  return (d && (d.message || d.error)) || ("后端返回 " + status);
}

const simTcp = ref({ x: 0, y: 0, z: 0 });
const cmdMode = ref("joint");
const cmdJoints = reactive([0, 0, 0, 0, 0, 0]);
const cmdCart = reactive({ x: 300, y: 0, z: 700, rx: "", ry: "", rz: "" });
const cmdResult = ref("输入目标后点「预演」——仅计算与动画，不下发机器人。");
const cmdResultCls = ref("");   // "" | ok | err
let cmdTimer = null;

// 操控模式：joint = 关节模式；cart = 机器人模式（末端 XYZ 移动，经后端 IK 反解）
const controlMode = ref("joint");
const cartAxis = ref(0);          // 0=X 1=Y 2=Z 3=A 4=B 5=C（埃夫特机器人坐标系六方向）
const ikMsg = ref("");
const cartBusy = ref(false);      // 一次 IK 请求进行中（防止连点叠加）
const canCartMove = computed(() => !cartBusy.value);

// =====================================================================
// 机器人模式（= 埃夫特手册的"机器人坐标系"= 笛卡尔）：**六个**滑动条
//
// 官方口径（EFORT 机器人学院 · 机器人坐标系下操作机器人）：
//   机器人坐标系是空间直角坐标系，除 XYZ 外还有 A/B/C 三个旋转方向描述绕 TCP 的转动：
//     X/Y/Z = 末端位置(mm)     A = 绕 Z 旋转     B = 绕 Y 旋转     C = 绕 X 旋转
//   示教器按键 1±=X 2±=Y 3±=Z 4±=A 5±=B 6±=C —— 所以这里是**六个滑块**（不是关节角）。
//   拖动任一滑块 = 末端沿该方向直线/定轴旋转 → 六个关节由 IK 一起联动（**整臂整体运动**）。
//
// ★ 位姿来源 `POST /control/fk`（后端 kinematics.pose_of）：与 /ik 同一套约定，
//   避免前端自算欧拉角导致"拖的方向不对"。
// ★ 姿态字段对应：A↔rz、B↔ry、C↔rx（ZYX 欧拉角，与后端 rpy_to_matrix 一致）。
// =====================================================================
const CART6 = [
  { i: 0, k: "X", k6: "x", unit: "mm", min: -900, max: 900, step: 10 },
  { i: 1, k: "Y", k6: "y", unit: "mm", min: -900, max: 900, step: 10 },
  { i: 2, k: "Z", k6: "z", unit: "mm", min: 0, max: 1400, step: 10 },
  { i: 3, k: "A", k6: "rz", unit: "°", min: -180, max: 180, step: 5 },
  { i: 4, k: "B", k6: "ry", unit: "°", min: -90, max: 90, step: 5 },
  { i: 5, k: "C", k6: "rx", unit: "°", min: -180, max: 180, step: 5 },
];
/** 当前末端位姿（DH 基座系：x/y/z mm + rx/ry/rz 度）—— 机器人模式滑块的"真值"。 */
const pose = reactive({ x: 0, y: 0, z: 0, rx: 0, ry: 0, rz: 0 });
// ★ 细节修复（用户指出"动一个，其余 5 个也跟着变"）：
//   ① poseReady：FK 没读到之前**绝不允许**下发六维目标 —— 否则 pose 全 0，
//      拖一个滑块会把其余 5 个一起送到 0（严重事故）。未就绪就禁用滑块并提示。
//   ② 姿态"等价表示"会跳变：同一姿态既可写成 (180,-90,0) 也可写成 (-165,…)
//      （万向锁 / ±180° 回绕）。回读后必须把角度**展开到与上一帧最近**的等价角，
//      否则只拖 X，A/C 的读数也会跳几十度。
const poseReady = ref(false);

/** 把角度回读值展开到与 prev 最近的等价角（±180° 回绕）。 */
function unwrapAngle(prev, val) {
  let v = val;
  while (v - prev > 180) v -= 360;
  while (v - prev < -180) v += 360;
  return v;
}

const selHud = computed(() => {
  if (controlMode.value === "cart") {
    const ax = CART6[cartAxis.value] || CART6[0];
    return `机器人模式 · 轴 ${ax.k}（${ax.unit}）  ·  末端 (${pose.x.toFixed(0)}, ${pose.y.toFixed(0)}, ${pose.z.toFixed(0)}) mm · `
      + `姿态 (${pose.rx.toFixed(1)}, ${pose.ry.toFixed(1)}, ${pose.rz.toFixed(1)})°`;
  }
  const lim = robot.limits[robot.selIdx] || { min: -180, max: 180 };
  return `关节模式 · J${robot.selIdx + 1} = ${robot.simQ[robot.selIdx].toFixed(1)}°  (限位 ${lim.min} ~ ${lim.max}°)`;
});

// =====================================================================
// XPL 仿真执行（仅仿真模式，不下发真机）
// =====================================================================
const xplFile = ref("");
const xplSteps = ref([]);
const xplRunning = ref(false);
const xplCurrentStep = ref(-1);
const xplError = ref("");
const xplSpeed = ref(1.0);  // 倍速
const xplSuck = ref(false); // 吸盘状态（仿真显示用，不下发任何 IO）
const xplFiles = ref([]);   // programs 目录下的程序文件清单（只读接口回的）
const xplPick = ref("");    // 当前选中的文件名

/** 拉一次 programs 目录清单（只读接口，失败静默 —— 可以继续手粘文本）。 */
async function refreshXplFiles() {
  try {
    const r = await fetch(apiUrl("/control/programs"));
    if (!r.ok) return;
    const d = await r.json();
    xplFiles.value = (d.items || []).filter((n) => n.toLowerCase().endsWith(".xpl"));
  } catch (e) { /* 相机/后端没起时不影响手粘文本这条路径 */ }
}

/** 从 programs 目录读文件 → 填进文本框（读完仍要点「解析」才变成 steps）。 */
async function pickXplFile() {
  if (!xplPick.value) return;
  xplError.value = "";
  try {
    const r = await fetch(apiUrl("/control/file-source?name=" + encodeURIComponent(xplPick.value)));
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { xplError.value = d.detail || ("读取失败 HTTP " + r.status); return; }
    xplFile.value = d.text || "";
    await loadXpl(d.name);
  } catch (e) {
    xplError.value = "读取文件失败: " + e.message;
  }
}

/**
 * 解析文本框内容 → steps。
 * @param {string} [name] 程序名（从文件来时传文件名，只用于显示）
 */
async function loadXpl(name) {
  const src = xplFile.value;
  if (!src.trim()) {
    xplSteps.value = [];
    xplError.value = "请先粘贴 XPL 文本，或从下方选择 programs 里的文件";
    return;
  }
  xplError.value = "";
  try {
    const { steps } = await parseXpl(src, name || xplPick.value || "手工粘贴");
    xplSteps.value = steps;
    xplCurrentStep.value = -1;
  } catch (e) {
    xplSteps.value = [];
    xplError.value = e.message;
  }
}

async function playXplStep(idx) {
  const s = xplSteps.value[idx];
  if (!s) return false;
  xplCurrentStep.value = idx;

  if (s.op === 'movej' && s.joints) {
    // 关节移动：直接设置目标，由 applySimPose 平滑过渡
    robot.setSimQ(s.joints);
  } else if (s.op === 'move' && s.tcp) {
    // 直角坐标移动：经 IK 反解。★ 必须 await —— 不等结果就往下走的话，
    //   IK 失败（不可达/超限）会被当成成功，仿真姿态停在上一步却继续跑后面。
    const t = s.tcp;
    const ok = await moveCartTo(t.x, t.y, t.z);
    if (!ok) {
      xplError.value = `第 ${idx + 1} 步 MOVE (${t.x}, ${t.y}, ${t.z}) 失败：${ikMsg.value}`;
      return false;
    }
  } else if (s.op === 'suck') {
    // 吸气/放气：仅仿真显示
    xplSuck.value = !!s.on;
  } else if (s.op === 'wait') {
    // 等待：暂停指定时间
    await new Promise(resolve => setTimeout(resolve, s.dwell_ms));
  } else if (s.op === 'unknown') {
    xplError.value = `第 ${idx + 1} 行指令未知: ${s.raw}`;
    return false;
  }
  return true;
}

async function runXpl() {
  if (xplRunning.value) return;
  if (!xplSteps.value.length) {
    xplError.value = "请先加载 XPL 文件";
    return;
  }
  xplRunning.value = true;
  xplError.value = "";
  try {
    for (let i = 0; i < xplSteps.value.length; i++) {
      if (!xplRunning.value) break;
      const ok = await playXplStep(i);
      if (ok === false) break;   // ★ 任一步失败即停，不把后面的动作叠在错位姿态上
      // 步间延迟（模拟真机执行节奏）
      await new Promise(r => setTimeout(r, 300 / xplSpeed.value));
    }
  } catch (e) {
    xplError.value = e.message;
  } finally {
    xplRunning.value = false;
    // ★ 保留 xplCurrentStep（=最后执行到的那步），界面上显示"已跑完第 N 步"；
    //   归 -1 会让"当前第几步"在结束后凭空消失，现场对不上节拍。
    xplSuck.value = false;
  }
}

function stopXpl() {
  xplRunning.value = false;
  xplCurrentStep.value = -1;
}

async function moveCartTo(x, y, z) {
  // XPL 的 MOVE 是**绝对坐标**：直接走 moveCartAbs，不复制一遍 IK 调用。
  return await moveCartAbs({ x, y, z });
}

onMounted(() => {
  window.addEventListener("keydown", onKey);
  refreshXplFiles();   // 只读清单：拿不到就当没有文件，仍可手粘文本
  refreshPose();       // 机器人模式六滑块初值 = 当前末端位姿（后端 FK）
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  if (cmdTimer) clearInterval(cmdTimer);
  if (tcpTimer) clearTimeout(tcpTimer);
  stopDance();
  robot.clearOverride("sim");
});

// 切到机器人模式时，把滑块拉到当前末端坐标（避免沿用上一次的旧目标）
watch(() => controlMode.value, (v) => { if (v === "cart") refreshPose(); });

function clampQ(i, v) {
  const lim = robot.limits[i] || { min: -180, max: 180 };
  return Math.min(lim.max, Math.max(lim.min, v));
}

// ★ Pose authority model: this page no longer drives 3D directly -- registers sandbox pose as override(owner="sim"),
//   only takes effect when this view is in foreground; App.vue render loop reads displayQ for 3D.
function applySimPose() {
  if (robot.activeView !== "sim") return;   // 单模型：只有本视图在前台才登记
  robot.setOverride("sim", robot.simQ, getTcp());
  simTcp.value = getTcp();
}

watch(() => robot.simQ, applySimPose, { deep: true, immediate: true });
// 切回模拟仿真时补一次姿态 + TCP；切走时停止跳舞并交还姿态（override 只认前台 owner，
// 但主动清掉更干净：回来时会由 immediate watch 重新登记）
watch(() => robot.activeView, (v) => {
  if (v === "sim") applySimPose();
  else { stopDance(); robot.clearOverride("sim"); }
});

function onKey(e) {
  if (robot.activeView !== "sim") return;
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  if (controlMode.value === "cart") return onKeyCart(e);
  const step = e.shiftKey ? 0.1 : 1.0;
  let handled = true;
  const q = robot.simQ.slice();
  if (e.key >= "1" && e.key <= "6") {
    robot.setSelIdx(parseInt(e.key, 10) - 1);
  } else if (e.key === "ArrowUp" || e.key === "+" || e.key === "=") {
    q[robot.selIdx] = clampQ(robot.selIdx, q[robot.selIdx] + step);
    robot.setSimQ(q);
  } else if (e.key === "ArrowDown" || e.key === "-" || e.key === "_") {
    q[robot.selIdx] = clampQ(robot.selIdx, q[robot.selIdx] - step);
    robot.setSimQ(q);
  } else if (e.key === "0") {
    robot.setSimQ([0, 0, 0, 0, 0, 0]);
  } else {
    handled = false;
  }
  if (handled) e.preventDefault();
}

// 机器人模式（= 埃夫特"机器人坐标系"）：1..6 选轴（X/Y/Z/A/B/C），↑↓ 沿轴走一步。
// 平移步进 10mm、姿态步进 5°（Shift 微调：1mm / 1°），全部经 IK 整臂联动。
function onKeyCart(e) {
  const ax = CART6[cartAxis.value] || CART6[0];
  const step = e.shiftKey
    ? (ax.unit === "mm" ? 1 : 1)
    : ax.step;
  let handled = true;
  if (e.key >= "1" && e.key <= "6") {
    cartAxis.value = parseInt(e.key, 10) - 1;
  } else if (e.key === "ArrowUp" || e.key === "+" || e.key === "=") {
    moveCart(cartAxis.value, +step);
  } else if (e.key === "ArrowDown" || e.key === "-" || e.key === "_") {
    moveCart(cartAxis.value, -step);
  } else {
    handled = false;
  }
  if (handled) e.preventDefault();
}

async function moveCart(axis, delta) {
  if (cartBusy.value) return;          // 防抖：上一次 IK 还没回来就别叠加
  cartBusy.value = true;
  try {
    const ax = CART6[axis] || CART6[0];
    pose[ax.k6] = Number((pose[ax.k6] + delta).toFixed(3));
    const ok = await applyCart();
    if (!ok) refreshPose();            // 不可达 → 回弹到真实位姿
  } finally {
    cartBusy.value = false;
  }
}

let tcpTimer = null;

/** 从后端取当前位姿（正运动学）：唯一真值来源，与 /ik 同一套约定。 */
async function refreshPose() {
  try {
    const r = await fetch(apiUrl("/control/fk"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ joints: robot.simQ }),
    });
    if (!r.ok) { poseReady.value = false; return false; }
    const d = await readJson(r);
    if (typeof d.x !== "number") { poseReady.value = false; return false; }
    pose.x = d.x; pose.y = d.y; pose.z = d.z;
    // ★ 姿态展开到与上一帧最近的等价角：避免"只拖 X，A/C 读数却跳几十度"
    pose.rx = unwrapAngle(pose.rx, d.rx);
    pose.ry = unwrapAngle(pose.ry, d.ry);
    pose.rz = unwrapAngle(pose.rz, d.rz);
    poseReady.value = true;
    return true;
  } catch (e) {
    poseReady.value = false;
    return false;
  }
}

/** 把当前 pose（六维）交给后端逆解 → 设回仿真姿态（整臂联动）。 */
async function applyCart() {
  // ★ 位姿未就绪（/control/fk 不可用）→ 绝不下发六维目标：
  //   否则 pose 里的 0 会被当成真值，"拖一个、其余五个全归零"。
  if (!poseReady.value) {
    ikMsg.value = "末端位姿未读取到（需后端 /control/fk，请重启后端）：暂不能按机器人坐标系移动";
    return false;
  }
  ikMsg.value = "计算中…";
  try {
    const r = await fetch(apiUrl("/control/ik"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current: robot.simQ,
        // 六维目标：位置 + 姿态（A/B/C 对应 rz/ry/rx）
        tcp: { x: pose.x, y: pose.y, z: pose.z, rx: pose.rx, ry: pose.ry, rz: pose.rz },
      }),
    });
    const d = await readJson(r);
    if (!r.ok) { ikMsg.value = "IK 请求失败：" + errText(d, r.status); return false; }
    if (d.joints && d.in_limits) {
      robot.setSimQ(d.joints);
      await refreshPose();             // 以真值刷新滑块（IK 有微小误差时也保持一致）
      ikMsg.value = `末端 (${pose.x.toFixed(0)}, ${pose.y.toFixed(0)}, ${pose.z.toFixed(0)}) mm · `
        + `姿态 (${pose.rx.toFixed(1)}, ${pose.ry.toFixed(1)}, ${pose.rz.toFixed(1)})°`;
      return true;
    }
    if (d.violations && d.violations.length) {
      ikMsg.value = "不可达/超限: " + d.violations.map((v) => v.joint).join(", ");
    } else {
      ikMsg.value = "IK 未收敛 (误差 " + (d.pos_err_mm || 0).toFixed(1) + " mm"
        + (d.rot_err_deg ? " / 姿态 " + d.rot_err_deg.toFixed(1) + "°" : "") + ")";
    }
    return false;
  } catch (err) {
    ikMsg.value = "IK 失败: " + err.message;
    return false;
  }
}

/** 滑块显示值：机器人模式看当前位姿；关节模式看末端位置（只读）。 */
function tcpAxisValue(i) {
  const ax = CART6[i] || CART6[0];
  if (controlMode.value === "cart") return Number(pose[ax.k6]);
  return i === 0 ? simTcp.value.x : i === 1 ? simTcp.value.y : i === 2 ? simTcp.value.z : 0;
}

/** 拖动中：更新目标并防抖 120ms 后解一次 IK（避免每个像素都打后端）。 */
function onTcpSlider(i, e) {
  const v = parseFloat(e.target.value);
  if (Number.isNaN(v)) return;
  const ax = CART6[i] || CART6[0];
  pose[ax.k6] = v;
  cartAxis.value = i;
  if (tcpTimer) clearTimeout(tcpTimer);
  tcpTimer = setTimeout(async () => {
    tcpTimer = null;
    const ok = await applyCart();
    if (!ok) refreshPose();            // 不可达 → 滑块回弹，绝不留下假值
  }, 120);
}

/**
 * 把末端送到 **three 世界系**的绝对坐标 th（mm）：three -> DH → /control/ik → 设回仿真姿态。
 * ★ 关节模式/机器人模式/XPL 的 MOVE 共用这一条通道 —— 坐标约定与错误处理只写一次。
 * @returns {Promise<boolean>} 是否成功落位（失败原因已写进 ikMsg）
 */
async function moveCartAbs(th) {
  // three -> DH 基座坐标（root 绕 X 转 -90°：dh.x=three.x, dh.y=-three.z, dh.z=three.y）
  const dh = { x: th.x, y: -th.z, z: th.y };
  ikMsg.value = "计算中…";
  // ★ 修复"机器人模式跟关节模式一模一样"：**优先用自由解 keep_orientation:false**。
  //   离线实测（_solve_tcp）：锁定朝向解在当前位形下是**退化解** ——
  //   末端只平移 10mm，手腕 J4/J6 却翻 ±90°/±180°，看起来就是"关节乱转"、TCP 几乎不动；
  //   自由解给出的是协调的小幅联动（J2/J3 各 ~1.7° 换来 10mm 平移），且 J6 基本不动 → 可预测。
  //   所以顺序反过来：先 free，free 解不出来（超限/不收敛）再退回锁定朝向。
  for (const keep of [false, true]) {
    try {
      const r = await fetch(apiUrl("/control/ik"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current: robot.simQ, tcp: dh, keep_orientation: keep }),
      });
      const d = await readJson(r);
      // ★ 先判 HTTP 状态再判业务字段：/ik 是**公开只读**接口（无需控制令牌），
      //   出问题时后端会给出结构化 detail，不能当成"没有 joints 字段"糊过去。
      if (!r.ok) { ikMsg.value = "IK 请求失败：" + errText(d, r.status); return false; }
      if (d.joints && d.in_limits) {
        robot.setSimQ(d.joints);
        return true;
      }
      // 锁定朝向失败 → 再试自由解；两次都失败才报错
      if (keep) continue;
      if (d.violations && d.violations.length) {
        ikMsg.value = "不可达/超限: " + d.violations.map((v) => v.joint).join(", ");
      } else {
        ikMsg.value = "IK 未收敛 (误差 " + (d.pos_err_mm || 0).toFixed(1) + " mm)";
      }
      return false;
    } catch (err) {
      ikMsg.value = "IK 失败: " + err.message;
      return false;
    }
  }
  return false;
}

/** 关节角滑块（本页"可操作"的关节角卡用）。 */
function onSlider(i, e) {
  const q = robot.simQ.slice();
  q[i] = parseFloat(e.target.value) || 0;
  robot.setSimQ(q);
}

// ---------- 指令预演 ----------
function fillCmd() {
  if (cmdMode.value === "joint") {
    for (let i = 0; i < 6; i++) cmdJoints[i] = robot.simQ[i];
  } else {
    const t = getTcp();
    cmdCart.x = +t.x.toFixed(1);
    cmdCart.y = +t.y.toFixed(1);
    cmdCart.z = +t.z.toFixed(1);
  }
}

/** 轨迹按时间线性插值（后端返回的 keyframe 序列，t 单位 ms）。 */
function lerpPose(frames, tMs) {
  if (tMs <= frames[0].t) return frames[0];
  const last = frames[frames.length - 1];
  if (tMs >= last.t) return last;
  let lo = 0, hi = frames.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t <= tMs) lo = mid; else hi = mid;
  }
  const a = frames[lo], b = frames[hi];
  const span = b.t - a.t || 1;
  const r = (tMs - a.t) / span;
  return {
    j1: a.j1 + (b.j1 - a.j1) * r, j2: a.j2 + (b.j2 - a.j2) * r,
    j3: a.j3 + (b.j3 - a.j3) * r, j4: a.j4 + (b.j4 - a.j4) * r,
    j5: a.j5 + (b.j5 - a.j5) * r, j6: a.j6 + (b.j6 - a.j6) * r,
  };
}

function playTrajectory(traj) {
  if (cmdTimer) clearInterval(cmdTimer);
  const total = traj[traj.length - 1].t || 1;
  const t0 = performance.now();
  cmdTimer = setInterval(() => {
    const el = performance.now() - t0;
    const p = lerpPose(traj, Math.min(el, total));
    robot.setSimQ([p.j1, p.j2, p.j3, p.j4, p.j5, p.j6]);
    if (el >= total) { clearInterval(cmdTimer); cmdTimer = null; }
  }, 40);
}

// ---------- 跳舞演示 ----------
// 轨迹定义在 utils/dance.js（单一来源）。
// J1 摆头 / J2·J3 舒展起伏 / J4 翻腕 / J5 点头 / J6 腕部摆动，平滑无跳变。
const dancing = ref(false);
let danceTimer = null;

function startDance() {
  if (robot.activeView !== "sim") return;
  stopDance();
  dancing.value = true;
  const t0 = performance.now();
  danceTimer = setInterval(() => {
    robot.setSimQ(dancePose((performance.now() - t0) / 1000));
  }, 30);
}

function stopDance() {
  if (danceTimer) { clearInterval(danceTimer); danceTimer = null; }
  dancing.value = false;
}

function renderResult(d) {
  const parts = [];
  if (d.violations && d.violations.length) {
    cmdResultCls.value = "err";
    parts.push("超限: " + d.violations.map((v) => `${v.joint}=${v.value}°(限 ${v.min}~${v.max})`).join(", "));
  } else {
    cmdResultCls.value = "ok";
    parts.push("限位通过");
  }
  if (d.solver && d.solver.type === "ik") {
    parts.push(`IK: 位置误差 ${d.solver.pos_err_mm}mm / 姿态 ${d.solver.rot_err_deg}° (${d.solver.iters} 次迭代)`);
  }
  parts.push(`目标关节: [${(d.target || []).map((v) => v.toFixed(1)).join(", ")}]`);
  if (d.tcp_end) parts.push(`末端: (${d.tcp_end.join(", ")}) mm · 位移 ${d.distance_mm}mm`);
  if (d.warnings && d.warnings.length) parts.push(d.warnings.join("；"));
  cmdResult.value = parts.join("<br>");
}

async function onPreview() {
  const body = {
    mode: cmdMode.value,
    current: robot.simQ.map((v) => +v.toFixed(3)),
    duration_ms: 2500,
    steps: 60,
  };
  if (cmdMode.value === "joint") {
    body.joints = [...cmdJoints];
  } else {
    body.tcp = { x: cmdCart.x, y: cmdCart.y, z: cmdCart.z };
    if (cmdCart.rx !== "" && cmdCart.ry !== "" && cmdCart.rz !== "") {
      body.tcp.rx = cmdCart.rx; body.tcp.ry = cmdCart.ry; body.tcp.rz = cmdCart.rz;
    }
  }
  cmdResultCls.value = "";
  cmdResult.value = "计算中…";
  try {
    const r = await fetch(apiUrl("/control/preview"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const d = await readJson(r);
    if (!r.ok) { cmdResultCls.value = "err"; cmdResult.value = "预演失败：" + errText(d, r.status); return; }
    if (d.error) { cmdResultCls.value = "err"; cmdResult.value = d.error; return; }
    renderResult(d);
    if (d.trajectory && d.trajectory.length) playTrajectory(d.trajectory);
  } catch (e) {
    cmdResultCls.value = "err";
    cmdResult.value = "预演失败: " + e.message;
  }
}
</script>

<template>
  <div class="sim-root">
    <MonitorLayout view="sim">
      <template #hud>
        <div class="sim-hud">{{ selHud }}</div>
      </template>
      <div class="side-cards">
        <!-- ★ 需求更正：本页移除的是 RobotParamsCard 的**只读**关节角读数卡
             （读数和 3D 姿态同源，属于"已读"展示）；**可操作**的关节角滑块卡
             保留在下方（"关节角 (deg)" 带 range 的那个）。
             TCP 读数由下方独立的"末端 TCP (mm)"卡（与 simTcp 同源）显示，故此处不再引用。 -->
        <div class="card">
          <h3>操控模式</h3>
          <!-- ★ 需求：只要两个按钮（关节模式 / 机器人模式），其余说明与状态行一律移除。
               仅保留"出错了才出现"的一行提示 —— 否则 IK 失败会被完全吞掉，用户只看到不动。 -->
          <div class="btns">
            <button :class="{ primary: controlMode === 'joint' }" @click="controlMode = 'joint'">关节模式</button>
            <button :class="{ primary: controlMode === 'cart' }" @click="controlMode = 'cart'">机器人模式</button>
          </div>
          <div v-if="ikMsg && ikMsg !== '计算中…'" class="small err-line">{{ ikMsg }}</div>
        </div>
      <div class="card xpl-card">
        <h3><Icon name="play" :size="15" /> XPL 仿真执行
          <span class="xpl-badge">仅仿真 · 不下发真机</span>
        </h3>

        <div class="xpl-field">
          <label class="xpl-label"><Icon name="file" :size="12" /> 选择程序文件</label>
          <select class="xpl-select" v-model="xplPick" @change="pickXplFile">
            <option value="">— 选择 .XPL 文件 —</option>
            <option v-for="n in xplFiles" :key="n" :value="n">{{ n }}</option>
          </select>
        </div>

        <div class="xpl-field">
          <label class="xpl-label"><Icon name="terminal" :size="12" /> XPL 内容
            <span class="xpl-hint">选文件自动填入（支持 Robox XML），或粘贴简化指令（MOVE / MOVEJ / SUCK / WAIT）</span>
          </label>
          <textarea class="xpl-ta" rows="7" v-model="xplFile"
                    placeholder="— 选择上方文件自动填入，或直接粘贴 XPL 文本 —"></textarea>
        </div>

        <div class="xpl-actions">
          <button class="xpl-btn" @click="loadXpl()" :disabled="!xplFile.trim()">
            <Icon name="upload" :size="13" /> 解析
          </button>
          <button class="xpl-btn primary" @click="runXpl" :disabled="xplRunning || !xplSteps.length">
            <Icon :name="xplRunning ? 'pause' : 'play'" :size="13" /> {{ xplRunning ? '运行中' : '运行' }}
          </button>
          <button class="xpl-btn" @click="stopXpl" :disabled="!xplRunning">
            <Icon name="stop" :size="13" /> 停止
          </button>
        </div>

        <div class="xpl-status">
          <span v-if="xplSteps.length">共 <b>{{ xplSteps.length }}</b> 步
            <template v-if="xplRunning && xplCurrentStep >= 0"> · 当前第 <b>{{ xplCurrentStep + 1 }}</b> 步</template>
            <template v-else-if="xplCurrentStep >= 0"> · 上次跑到第 <b>{{ xplCurrentStep + 1 }}</b> 步</template>
          </span>
          <span v-else class="small">尚未解析：先选文件或粘贴文本，再点「解析」。</span>
          <span v-if="xplSuck" class="xpl-suck">吸气中</span>
        </div>

        <div v-if="xplError" class="xpl-err">
          <Icon name="alert" :size="13" /> {{ xplError }}
        </div>

        <div class="xpl-speed">
          <label class="xpl-label">倍速</label>
          <input type="range" min="0.25" max="4" step="0.25" v-model.number="xplSpeed" />
          <span class="xpl-speed-v">{{ xplSpeed }}×</span>
        </div>
      </div>

      <div class="card">
        <h3>演示动作</h3>
        <div class="btns">
          <button class="primary" :disabled="dancing" @click="startDance"><Icon name="play" :size="15" /> 跳舞演示</button>
          <button :disabled="!dancing" @click="stopDance"><Icon name="stop" :size="15" /> 停止</button>
        </div>
        <div class="small" style="margin-top:8px">
          一段预设的关节轨迹（J1 摆头 / J2·J3 舒展起伏 / J4 翻腕 / J5 点头 / J6 腕部摆动），
          经姿态平滑丝滑循环。
        </div>
      </div>
      <!-- ★ 需求更正：本页要保留的"关节角 (deg)"是**可操作**的滑块卡（下方），
           被移除的应是 RobotParamsCard 里那份**只读**读数卡（见文件头部卡片区）。
           ★ 且只在「关节模式」出现：机器人模式改用下面的 TCP 点动卡，
             否则两种模式共用同一套滑块，看起来"一模一样"。 -->
      <div class="card" v-if="controlMode === 'joint'">
        <h3>关节角 (deg)</h3>
        <div class="axis" v-for="(n, i) in robot.axes" :key="n">
          <label>{{ n }}</label>
          <input type="range" :min="robot.limits[i]?.min ?? -180" :max="robot.limits[i]?.max ?? 180"
                 step="0.1" :value="robot.simQ[i]" @input="onSlider(i, $event)" />
          <span class="deg">{{ robot.simQ[i].toFixed(1) }}</span>
        </div>
      </div>
      <div class="card">
        <h3>指令预演 (只算不发)</h3>
        <div class="btns" style="margin-bottom:8px">
          <button :class="{ primary: cmdMode === 'joint' }" @click="cmdMode = 'joint'">关节模式</button>
          <button :class="{ primary: cmdMode === 'cart' }" @click="cmdMode = 'cart'">直角模式</button>
        </div>
        <template v-if="cmdMode === 'joint'">
          <div class="row" v-for="(n, i) in robot.axes" :key="n">
            <span class="k">{{ n }}</span><input type="number" v-model.number="cmdJoints[i]" step="5" />
          </div>
        </template>
        <template v-else>
          <div class="row"><span class="k">X (mm)</span><input type="number" v-model.number="cmdCart.x" step="10" /></div>
          <div class="row"><span class="k">Y (mm)</span><input type="number" v-model.number="cmdCart.y" step="10" /></div>
          <div class="row"><span class="k">Z (mm)</span><input type="number" v-model.number="cmdCart.z" step="10" /></div>
          <div class="row"><span class="k">姿态 RPY(°) 可选</span>
            <span>
              <input type="number" v-model.number="cmdCart.rx" placeholder="RX" step="15" style="width:48px" />
              <input type="number" v-model.number="cmdCart.ry" placeholder="RY" step="15" style="width:48px" />
              <input type="number" v-model.number="cmdCart.rz" placeholder="RZ" step="15" style="width:48px" />
            </span>
          </div>
        </template>
        <div class="btns" style="margin-top:8px">
          <button @click="fillCmd">取当前姿态</button>
          <button class="primary" @click="onPreview">预演</button>
        </div>
        <div class="cmd-result small" :class="cmdResultCls" style="margin-top:8px">
          <Icon v-if="cmdResultCls === 'ok'" name="check" :size="14" class="cr-ico" />
          <Icon v-else-if="cmdResultCls === 'err'" name="alert" :size="14" class="cr-ico" />
          <span v-html="cmdResult"></span>
        </div>
      </div>
      <div class="card">
        <h3>末端 TCP (mm)
          <span v-if="controlMode === 'cart'" class="h3-sub">机器人模式 · 拖滑块整臂联动</span>
          <span v-else class="h3-sub">只读</span>
        </h3>

        <!-- 关节模式：末端坐标只读 -->
        <template v-if="controlMode !== 'cart'">
          <div class="row"><span class="k">X</span><span class="v">{{ simTcp.x.toFixed(1) }}</span></div>
          <div class="row"><span class="k">Y</span><span class="v">{{ simTcp.y.toFixed(1) }}</span></div>
          <div class="row"><span class="k">Z</span><span class="v">{{ simTcp.z.toFixed(1) }}</span></div>
        </template>

        <!-- ★ 机器人模式（= 埃夫特手册的"机器人坐标系"= 笛卡尔）：**六个**滑块
             X/Y/Z(位置 mm) + A/B/C(姿态 °，A绕Z、B绕Y、C绕X)。
             拖任一滑块 → 末端沿该方向直线/定轴旋转 → 六关节由 IK 一起联动（整臂整体运动）。 -->
        <template v-else>
          <p v-if="!poseReady" class="small" style="margin-top:8px; color: var(--err)">
            ⚠ 末端位姿读取失败（需要后端 <b>POST /control/fk</b>，请重启后端）：六滑块暂不可用。
          </p>
          <div class="axis" v-for="ax in CART6" :key="ax.k"
               :class="{ hot: cartAxis === ax.i }">
            <label>{{ ax.k }}</label>
            <input type="range" :min="ax.min" :max="ax.max" :step="ax.step"
                   :value="tcpAxisValue(ax.i)" :disabled="!canCartMove || !poseReady"
                   @input="onTcpSlider(ax.i, $event)" />
            <span class="deg">{{ tcpAxisValue(ax.i).toFixed(1) }}</span>
          </div>
          <div class="btns" style="margin-top:8px">
            <button v-for="(ax, i) in CART6" :key="ax.k"
                    :class="{ primary: cartAxis === i }" @click="cartAxis = i">{{ ax.k }}</button>
          </div>
          <div class="btns" style="margin-top:6px">
            <button :disabled="!canCartMove || !poseReady" @click="moveCart(cartAxis, -(CART6[cartAxis] || CART6[0]).step)">
              − {{ (CART6[cartAxis] || CART6[0]).step }}{{ (CART6[cartAxis] || CART6[0]).unit }}
            </button>
            <button :disabled="!canCartMove || !poseReady" @click="moveCart(cartAxis, (CART6[cartAxis] || CART6[0]).step)">
              + {{ (CART6[cartAxis] || CART6[0]).step }}{{ (CART6[cartAxis] || CART6[0]).unit }}
            </button>
          </div>
          <p class="small" style="margin-top:8px">
            按键 <kbd>1</kbd>~<kbd>6</kbd> 选轴（X/Y/Z/A/B/C）· <kbd>↑</kbd>/<kbd>↓</kbd> 沿轴走一步 ·
            <kbd>Shift</kbd> 微调。姿态：A 绕 Z、B 绕 Y、C 绕 X（埃夫特机器人坐标系）。
          </p>
          <p class="small" style="margin-top:6px">
            ⚠ 奇异点：<b>J5 ≈ 0（腕部奇异）</b>等位形下机器人坐标系不能继续移动（IK 会报不可达/乱解），
            按手册要求**切回关节坐标系挪开**再继续。
          </p>
          <p class="small" style="margin-top:6px">整臂联动（六轴同时变化）：</p>
          <div class="linkq">
            <span v-for="(n, i) in robot.axes" :key="n">{{ n }} <b>{{ robot.simQ[i].toFixed(1) }}°</b></span>
          </div>
        </template>
      </div>
    </div>
    </MonitorLayout>
  </div>
</template>

<style scoped>
.sim-root { position: relative; flex: 1; min-height: 0; display: flex; }
/* 左侧浮动物体管理面板：可折叠，靠在 3D 视口左上、避开顶部 HUD 一行 */
.sim-floating { position: absolute; left: 12px; top: 44px; width: 286px; z-index: 30;
  max-height: calc(100% - 60px); overflow-y: auto; }

/* ===== XPL 仿真执行卡片：纵向分区，层次清晰、控件不挤压 ===== */
.xpl-card :deep(h3) { display: flex; align-items: center; gap: 6px; }
.xpl-badge {
  margin-left: auto; font-size: 10px; font-weight: 500; color: var(--warn);
  border: 1px solid var(--warn-line); background: var(--warn-soft);
  border-radius: 20px; padding: 1px 8px; white-space: nowrap;
}
.xpl-field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 10px; }
.xpl-label { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); }
.xpl-hint { color: var(--muted); opacity: .8; font-size: 10px; line-height: 1.4; }
.xpl-select { width: 100%; background: var(--panel2); color: var(--txt);
  border: 1px solid var(--line); border-radius: 7px; padding: 7px 9px; font-size: 12px;
  font-family: inherit; }
.xpl-select:focus { outline: none; border-color: var(--accent); }
.xpl-ta { width: 100%; resize: vertical; font-family: Consolas, monospace; font-size: 11.5px;
  line-height: 1.55; min-height: 96px; box-sizing: border-box;
  background: var(--panel2); color: var(--txt); border: 1px solid var(--line);
  border-radius: 7px; padding: 8px 10px; }
.xpl-ta:focus { outline: none; border-color: var(--accent); }
.xpl-actions { display: flex; gap: 8px; margin-bottom: 8px; }
.xpl-actions .xpl-btn { flex: 1 1 0; justify-content: center; display: inline-flex;
  align-items: center; gap: 5px; padding: 8px 10px; }
.xpl-status { display: flex; align-items: center; gap: 8px; font-size: 11px;
  color: var(--txt); min-height: 18px; flex-wrap: wrap; }
.xpl-status b { color: var(--accent); }
.xpl-suck { font-size: 10px; color: var(--ok); border: 1px solid var(--ok-line);
  background: var(--ok-soft); border-radius: 20px; padding: 1px 8px; }
.xpl-err { display: flex; align-items: center; gap: 6px; margin-top: 8px; font-size: 11px;
  color: var(--err); border: 1px solid var(--err-line, var(--line)); background: var(--err-soft, var(--panel2));
  border-radius: 7px; padding: 7px 9px; }
.xpl-speed { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.xpl-speed input[type="range"] { flex: 1; accent-color: var(--accent); }
.xpl-speed-v { width: 44px; text-align: right; font-variant-numeric: tabular-nums;
  font-size: 12px; color: var(--txt); }

/* 操控模式里的"仅出错才显示"提示行 */
.err-line { margin-top: 8px; color: var(--err); }
/* 机器人模式：当前点动轴那一行高亮 */
.row.hot { background: var(--accent-soft2); border-radius: 4px; }
/* 机器人模式：六轴联动结果（整臂运动的佐证） */
.linkq { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 4px;
  font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.linkq b { color: var(--accent); }
</style>
