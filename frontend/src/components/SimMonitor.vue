<script setup>
// 模拟仿真视图：键盘控制 + 关节/直角指令预演（只算不发）+ 外观切换 + 录制。
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useRobotStore } from "../stores/robot.js";
import { useRecordingStore } from "../stores/recording.js";
import {
  applyRobotPose, getTcp, setShowGlb, getShowGlb, setGripperOpen,
} from "../three/manager.js";
import { apiUrl } from "../config.js";
import { dancePose } from "../utils/dance.js";
import MonitorLayout from "./MonitorLayout.vue";

const robot = useRobotStore();
const rec = useRecordingStore();

const showGlb = ref(getShowGlb());   // 与 manager 的当前状态对齐(默认官方真机数模)
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
});

function clampQ(i, v) {
  const lim = robot.limits[i] || { min: -180, max: 180 };
  return Math.min(lim.max, Math.max(lim.min, v));
}

function applySimPose() {
  if (robot.activeView !== "sim") return;   // 单模型：只有本视图在前台才驱动姿态
  applyRobotPose(robot.simQ);
  simTcp.value = getTcp();
}

watch(() => robot.simQ, applySimPose, { deep: true, immediate: true });
// 切回模拟仿真时补一次姿态 + TCP；切走时停止跳舞
watch(() => robot.activeView, (v) => {
  if (v === "sim") applySimPose();
  else stopDance();
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
    const d = await r.json();
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

function toggleGlb(v) {
  showGlb.value = v;
  setShowGlb(v);
  setGripperOpen(gripperOpen.value);   // 切到官方数模时同步夹爪开度
}

// J6 末端夹爪开度(mm)，仅官方数模带末端装置
const gripperOpen = ref(46);
function onGripper(e) {
  gripperOpen.value = parseFloat(e.target.value) || 0;
  setGripperOpen(gripperOpen.value);
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

function playTrajectory(traj) {
  if (cmdTimer) clearInterval(cmdTimer);
  const total = traj[traj.length - 1].t || 1;
  const t0 = performance.now();
  cmdTimer = setInterval(() => {
    const el = performance.now() - t0;
    const p = rec.lerpPose(traj, Math.min(el, total));
    robot.setSimQ([p.j1, p.j2, p.j3, p.j4, p.j5, p.j6]);
    if (el >= total) { clearInterval(cmdTimer); cmdTimer = null; }
  }, 40);
}

// ---------- 跳舞演示 ----------
// 轨迹定义在 utils/dance.js —— 与「真实监控离线演示」共用同一段动作（单一来源）。
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
    parts.push("✗ 超限: " + d.violations.map((v) => `${v.joint}=${v.value}°(限 ${v.min}~${v.max})`).join(", "));
  } else {
    cmdResultCls.value = "ok";
    parts.push("✓ 限位通过");
  }
  if (d.solver && d.solver.type === "ik") {
    parts.push(`IK: 位置误差 ${d.solver.pos_err_mm}mm / 姿态 ${d.solver.rot_err_deg}° (${d.solver.iters} 次迭代)`);
  }
  parts.push(`目标关节: [${(d.target || []).map((v) => v.toFixed(1)).join(", ")}]`);
  if (d.tcp_end) parts.push(`末端: (${d.tcp_end.join(", ")}) mm · 位移 ${d.distance_mm}mm`);
  if (d.warnings && d.warnings.length) parts.push("⚠ " + d.warnings.join("；"));
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
    const d = await r.json();
    if (d.error) { cmdResultCls.value = "err"; cmdResult.value = "✗ " + d.error; return; }
    renderResult(d);
    if (d.trajectory && d.trajectory.length) playTrajectory(d.trajectory);
  } catch (e) {
    cmdResultCls.value = "err";
    cmdResult.value = "预演失败: " + e.message;
  }
}
</script>

<template>
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
        <h3>录制控制</h3>
        <div class="btns">
          <button :disabled="rec.recOn" @click="rec.startRecording('sim')">⏺ 录制</button>
          <button :disabled="!rec.recOn || rec.recorder.source !== 'sim'" @click="rec.stopRecording()">⏹ 停止</button>
        </div>
        <div class="rec-state">
          状态: <b>{{ rec.recOn && rec.recorder.source === 'sim' ? `录制中 · 已录 ${rec.recorder.frames.length} 帧` : '空闲' }}</b>
        </div>
        <div class="small" style="margin-top:6px">录制期间用键盘/滑块摆动作，停止后自动保存到「录制回放」库。</div>
      </div>
      <div class="card">
        <h3>演示动作</h3>
        <div class="btns">
          <button class="primary" :disabled="dancing" @click="startDance">🕺 跳舞演示</button>
          <button :disabled="!dancing" @click="stopDance">⏹ 停止</button>
        </div>
        <div class="small" style="margin-top:8px">
          一段预设的关节轨迹（J1 摆头 / J2·J3 舒展起伏 / J4 翻腕 / J5 点头 / J6 腕部摆动），
          经姿态平滑丝滑循环。真实监控离线时也播放同一段动作。
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
        <h3>外观</h3>
        <div class="btns">
          <button :class="{ primary: !showGlb }" @click="toggleGlb(false)">程序化(可动)</button>
          <button :class="{ primary: showGlb }" @click="toggleGlb(true)">官方数模(可动)</button>
        </div>
        <div class="small" style="margin-top:8px">
          「官方数模」= ER8-700H 官方 STEP 实物外观，已按 DH 关节轴重挂，六轴跟随关节角运动，
          并按真机配色重新上色（白漆机身 / 深灰五金 / 铝银法兰）。<b>真实监控与本页共用这一套。</b>
        </div>
        <div v-if="showGlb" style="margin-top:10px">
          <div class="row"><span class="k">J6 末端夹爪</span><span class="v">{{ gripperOpen.toFixed(0) }} mm 开度</span></div>
          <input type="range" min="30" max="160" step="1" :value="gripperOpen" @input="onGripper" />
          <div class="small">官方数模带 J6 气动平行夹爪（含法兰适配盘/气管/警示条），随 J6 一起运动。</div>
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
        <div class="cmd-result small" :class="cmdResultCls" style="margin-top:8px" v-html="cmdResult"></div>
      </div>
      <div class="card">
        <h3>末端 TCP (mm)</h3>
        <div class="row"><span class="k">X</span><span class="v">{{ simTcp.x.toFixed(1) }}</span></div>
        <div class="row"><span class="k">Y</span><span class="v">{{ simTcp.y.toFixed(1) }}</span></div>
        <div class="row"><span class="k">Z</span><span class="v">{{ simTcp.z.toFixed(1) }}</span></div>
      </div>
    </div>
  </MonitorLayout>
</template>
