<script setup>
// 模拟仿真视图：键盘控制 + 关节/直角指令预演（只算不发）。
// ★ 外观固定使用官方数模（ER8-700H GLB 可动版），本页不再提供模型切换。
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useRobotStore } from "../stores/robot.js";
import { getTcp } from "../three/manager.js";
import { apiUrl } from "../config.js";
import { dancePose } from "../utils/dance.js";
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
const cartAxis = ref(0);          // 0=X, 1=Y, 2=Z
const ikMsg = ref("");
const CART_STEP = 10;             // 步进 mm
const CART_AXES = ["X", "Y", "Z"];

const selHud = computed(() => {
  if (controlMode.value === "cart") {
    const t = simTcp.value;
    return `机器人模式 · 移动轴 ${CART_AXES[cartAxis.value]}  ·  末端 (${t.x.toFixed(0)}, ${t.y.toFixed(0)}, ${t.z.toFixed(0)}) mm`;
  }
  const lim = robot.limits[robot.selIdx] || { min: -180, max: 180 };
  return `关节模式 · J${robot.selIdx + 1} = ${robot.simQ[robot.selIdx].toFixed(1)}°  (限位 ${lim.min} ~ ${lim.max}°)`;
});

onMounted(() => {
  window.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  if (cmdTimer) clearInterval(cmdTimer);
  stopDance();
  robot.clearOverride("sim");
});

function clampQ(i, v) {
  const lim = robot.limits[i] || { min: -180, max: 180 };
  return Math.min(lim.max, Math.max(lim.min, v));
}

// ★ 姿态权威模型：本页不再直接驱动 3D —— 把沙盘姿态登记为 override(owner="sim")，
//   只有本页在前台时生效；App.vue 的渲染循环统一读取 displayQ 下发到 3D。
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

// 机器人模式：1/2/3 选 X/Y/Z，↑↓ 沿轴移动，经后端 IK 反解关节角
function onKeyCart(e) {
  const step = e.shiftKey ? 1 : CART_STEP;
  let handled = true;
  if (e.key >= "1" && e.key <= "3") {
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
  const t = getTcp();          // three 世界坐标（mm），与界面"末端 TCP"显示一致
  const th = { x: t.x, y: t.y, z: t.z };
  if (axis === 0) th.x += delta;
  else if (axis === 1) th.y += delta;
  else th.z += delta;
  // three -> DH 基座坐标（root 绕 X 转 -90°：dh.x=three.x, dh.y=-three.z, dh.z=three.y）
  const dh = { x: th.x, y: -th.z, z: th.y };
  ikMsg.value = "计算中…";
  try {
    const r = await fetch(apiUrl("/control/ik"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current: robot.simQ, tcp: dh, keep_orientation: false }),
    });
    const d = await readJson(r);
    // ★ 先判 HTTP 状态再判业务字段：/ik 是**公开只读**接口（无需控制令牌），
    //   出问题时后端会给出结构化 detail，不能当成"没有 joints 字段"糊过去。
    if (!r.ok) { ikMsg.value = "IK 请求失败：" + errText(d, r.status); return; }
    if (d.joints && d.in_limits) {
      robot.setSimQ(d.joints);
      ikMsg.value = `末端 → (${th.x.toFixed(0)}, ${th.y.toFixed(0)}, ${th.z.toFixed(0)}) mm`;
    } else if (d.violations && d.violations.length) {
      ikMsg.value = "不可达/超限: " + d.violations.map((v) => v.joint).join(", ");
    } else {
      ikMsg.value = "IK 未收敛 (误差 " + (d.pos_err_mm || 0).toFixed(1) + " mm)";
    }
  } catch (err) {
    ikMsg.value = "IK 失败: " + err.message;
  }
}

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
        <div class="card">
          <h3>操控模式</h3>
        <div class="btns" style="margin-bottom:8px">
          <button :class="{ primary: controlMode === 'joint' }" @click="controlMode = 'joint'">关节模式</button>
          <button :class="{ primary: controlMode === 'cart' }" @click="controlMode = 'cart'">机器人模式</button>
        </div>
        <template v-if="controlMode === 'joint'">
          <div class="small" style="margin-bottom:8px">
            <kbd>1</kbd>~<kbd>6</kbd> 选择关节 ·
            <kbd>↑</kbd>/<kbd>↓</kbd> 步进 ±1° ·
            <kbd>Shift</kbd>+方向键 微调 ±0.1° ·
            <kbd>0</kbd> 全部归零
          </div>
        </template>
        <template v-else>
          <div class="small" style="margin-bottom:8px">
            <kbd>1</kbd>/<kbd>2</kbd>/<kbd>3</kbd> 选 X/Y/Z 轴 ·
            <kbd>↑</kbd>/<kbd>↓</kbd> 移动 ±10mm ·
            <kbd>Shift</kbd> 微调 ±1mm
          </div>
          <div class="small">当前移动轴：<b>{{ CART_AXES[cartAxis] }}</b>（末端经 IK 反解）</div>
          <div class="small" style="margin-top:4px">{{ ikMsg }}</div>
        </template>
        <div class="status"><span class="dot warn"></span><span>就绪（只算不发）</span></div>
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
      <div class="card">
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
        <h3>末端 TCP (mm)</h3>
        <div class="row"><span class="k">X</span><span class="v">{{ simTcp.x.toFixed(1) }}</span></div>
        <div class="row"><span class="k">Y</span><span class="v">{{ simTcp.y.toFixed(1) }}</span></div>
        <div class="row"><span class="k">Z</span><span class="v">{{ simTcp.z.toFixed(1) }}</span></div>
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
</style>
