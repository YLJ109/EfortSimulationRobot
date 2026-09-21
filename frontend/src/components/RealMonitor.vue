<script setup>
// 真实监控视图：3D 场景 + HUD + 侧栏（连接 / 离线演示动作 / 录制 / 关节滑块 / TCP）。
//
// ★ 离线演示：机器人没连上（未连接 或 后端降级为模拟）时，本视图不显示后端那套
//   模拟姿态，而是自动播放与「模拟仿真」相同的跳舞动作（utils/dance.js，单一来源）。
//   演示期间由本地动画独占姿态（store.localDemo 抑制远端姿态写入），避免两个写者
//   互相抢姿态导致抖动；配合 manager 的姿态平滑 → 丝滑循环。
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useRobotStore } from "../stores/robot.js";
import { useRecordingStore } from "../stores/recording.js";
import { applyRobotPose, getTcp } from "../three/manager.js";
import { dancePose } from "../utils/dance.js";
import { apiUrl } from "../config.js";
import JointHud from "./JointHud.vue";
import MonitorLayout from "./MonitorLayout.vue";

const robot = useRobotStore();
const rec = useRecordingStore();

const followLive = ref(true);
const qLive = reactive([0, 0, 0, 0, 0, 0]);
const reconnecting = ref(false);
const reconnectMsg = ref("");

// 离线/模拟状态：未连接，或后端已降级为模拟数据
const offline = computed(() => !robot.connected || robot.simulated);

// ---------- 离线演示动作（与「模拟仿真」共用同一段跳舞轨迹） ----------
// ★ 机器人离线时，真实监控的姿态**完全由前端管理**：要么播放跳舞演示，要么冻结在最后一帧。
//   这段时间一律忽略后端那套"模拟姿态"（否则点暂停后会立刻被后端的模拟动作接管，
//   而它各轴摆幅很大、J6 能扫到 ±216° → 看起来就是"疯狂的转动"）。
const dancing = ref(false);
const demoOn = ref(true);        // 离线时是否自动演示（点暂停 → 冻结）
let danceTimer = null;

function startDanceTimer() {
  if (danceTimer) return;
  if (robot.activeView !== "live" || !offline.value || !demoOn.value || !followLive.value) return;
  dancing.value = true;
  const t0 = performance.now();
  danceTimer = setInterval(() => {
    if (robot.activeView !== "live" || !offline.value) { stopDanceTimer(); return; }
    const q = dancePose((performance.now() - t0) / 1000);
    applyRobotPose(q);           // 直接驱动 3D（唯一写者 + 平滑 → 丝滑）
    robot.latestTcp = getTcp();  // TCP 卡片按当前目标姿态刷新
    robot.latestPose = q;        // 同步 HUD / 关节滑块（下方 watch 会再补一次，幂等）
  }, 30);
}

function stopDanceTimer() {
  if (danceTimer) { clearInterval(danceTimer); danceTimer = null; }
  dancing.value = false;
}

/**
 * 统一同步"姿态来源"：
 *   - 离线 → `localDemo=true`：前端独占姿态（抑制远端模拟），并决定是否播放演示；
 *   - 在线 → `localDemo=false`：交还远端真实姿态，实时跟随。
 * ★ 关键：`localDemo` 只跟"是否离线"绑定，**不跟"是否正在跳舞"绑定** ——
 *   所以点暂停时只是停掉定时器，姿态仍由前端持有 → 冻结不动，而不是被后端模拟接管。
 */
function syncDance() {
  robot.setLocalDemo(offline.value);
  const should = robot.activeView === "live" && offline.value && demoOn.value && followLive.value;
  if (should) startDanceTimer();
  else stopDanceTimer();
}

function playDemo() { followLive.value = true; demoOn.value = true; syncDance(); }
function pauseDemo() { demoOn.value = false; syncDance(); }   // 暂停 → 冻结当前姿态

// 单模型：只有本视图在前台才把姿态推给 3D 场景，避免覆盖其它视图
function applyLive() {
  if (robot.activeView !== "live") return;
  applyRobotPose([...qLive]);
}

function onSlider(i, e) {
  qLive[i] = parseFloat(e.target.value) || 0;
  followLive.value = false;      // 手动摆姿 → 暂停跟随与离线演示
  syncDance();
  applyLive();
}

// 手动重连机器人（网线在摄像头/机器人间切换后，点此触发，无需重启）
async function reconnectRobot() {
  if (reconnecting.value) return;
  reconnecting.value = true;
  reconnectMsg.value = "重连中…";
  try {
    const r = await fetch(apiUrl("/reconnect"), { method: "POST" });
    const d = await r.json();
    robot.connected = d.connected;
    robot.simulated = d.simulated;
    reconnectMsg.value = d.connected && !d.simulated ? "已连上机器人(真实)" : "机器人不可达(保持模拟)";
  } catch (e) {
    reconnectMsg.value = "重连失败: " + e.message;
  } finally {
    reconnecting.value = false;
    syncDance();                 // 重连后按新状态决定起停
  }
}

// 远端姿态（在线时）驱动 HUD + 3D；离线演示时 latestPose 由本地动画写入，同样走这里
watch(() => robot.latestPose, (q) => {
  if (!q) return;
  if (followLive.value) {
    for (let i = 0; i < 6; i++) qLive[i] = q[i];
    applyLive();
  }
});

// 连接状态 / 视图切换 → 重算是否播演示；切回真实监控且未演示时补一次姿态
watch([() => robot.activeView, offline], () => {
  syncDance();
  if (robot.activeView === "live" && !dancing.value) applyLive();
});

onMounted(() => { syncDance(); });
onBeforeUnmount(() => { stopDanceTimer(); robot.setLocalDemo(false); });
</script>

<template>
  <MonitorLayout view="live">
    <template #hud>
      <JointHud />
    </template>
    <div class="side-cards">
      <div class="card">
        <h3>连接状态</h3>
        <div class="status">
          <span class="dot" :class="robot.connected ? (robot.simulated ? 'warn' : 'ok') : 'err'"></span>
          <span>{{ robot.connected ? (robot.simulated ? '已连接(模拟)' : '已连接(真实)') : '连接断开, 重连中…' }}</span>
        </div>
        <div class="row"><span class="k">机型</span><span class="v">{{ robot.robotName }}</span></div>
        <div class="row"><span class="k">数据来源</span><span class="v">{{ robot.simulated ? '模拟(离线)' : '真实 Modbus' }}</span></div>
        <div class="btns" style="margin-top:8px">
          <button :disabled="reconnecting" @click="reconnectRobot">↻ 重连机器人</button>
        </div>
        <div v-if="reconnectMsg" class="small" style="margin-top:6px">{{ reconnectMsg }}</div>
        <div v-if="robot.calibrationPending" class="calib">⚠ DH 尚未校准，形态为近似值</div>
      </div>
      <div class="card">
        <h3>离线演示动作</h3>
        <div class="status">
          <span class="dot" :class="offline ? 'warn' : 'ok'"></span>
          <span>{{ offline ? (dancing ? '离线演示运行中' : '离线演示已暂停') : '已连接真实机器人' }}</span>
        </div>
        <div class="btns" style="margin-top:8px">
          <button class="primary" :disabled="!offline || dancing" @click="playDemo">🕺 播放演示</button>
          <button :disabled="!dancing" @click="pauseDemo">⏹ 暂停演示</button>
        </div>
        <div class="small" style="margin-top:6px">
          机器人未连接时，自动播放与「模拟仿真」相同的跳舞动作（丝滑循环）；
          <b>点「暂停」会冻结在最后一帧</b>（不会被后端的模拟动作接管）；连上真机后自动转为实时跟随。
        </div>
      </div>
      <div class="card">
        <h3>录制控制</h3>
        <div class="btns">
          <button :disabled="rec.recOn" @click="rec.startRecording('real')">⏺ 录制</button>
          <button :disabled="!rec.recOn || rec.recorder.source !== 'real'" @click="rec.stopRecording()">⏹ 停止</button>
        </div>
        <div class="rec-state">
          状态: <b>{{ rec.recOn && rec.recorder.source === 'real' ? `录制中 · 已录 ${rec.recorder.frames.length} 帧` : '空闲' }}</b>
        </div>
        <div class="small" style="margin-top:6px">录制真机实时动作，停止后自动保存到「录制回放」库。</div>
      </div>
      <div class="card">
        <h3>关节角 (deg)</h3>
        <div class="axis" v-for="(n, i) in robot.axes" :key="n">
          <label>{{ n }}</label>
          <input type="range" min="-180" max="180" step="0.1" :value="qLive[i]" @input="onSlider(i, $event)" />
          <span class="deg">{{ qLive[i].toFixed(1) }}</span>
        </div>
        <div class="calib" style="margin-top:8px">提示：拖动滑块可手动摆姿态（演示，会暂停离线演示动作）</div>
      </div>
      <div class="card">
        <h3>末端 TCP (mm)</h3>
        <div class="row"><span class="k">X</span><span class="v">{{ robot.tcp.x.toFixed(1) }}</span></div>
        <div class="row"><span class="k">Y</span><span class="v">{{ robot.tcp.y.toFixed(1) }}</span></div>
        <div class="row"><span class="k">Z</span><span class="v">{{ robot.tcp.z.toFixed(1) }}</span></div>
      </div>
    </div>
  </MonitorLayout>
</template>
