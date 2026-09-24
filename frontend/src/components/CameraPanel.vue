<script setup>
// 摄像头面板：一键启停 + 颜色分拣 + 截图 / 重连。
// 浮在 3D 视口内，可拖动标题栏移位、拖右下角缩放；位置/尺寸记忆到 localStorage。
//
// ★ 本组件在四个视图里各挂一份（真实监控 / 点位执行 / 程序执行），全部 v-show 保活。
//   因此"我是不是当前活跃的那一份"必须判准，否则会同时打出多条 MJPEG 连接。
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useCameraStore } from "../stores/camera.js";
import { useRobotStore } from "../stores/robot.js";
import { cameraBase } from "../config.js";
import Icon from "./Icon.vue";

const props = defineProps({
  views: { type: Array, default: () => ["live"] },
});

const cam = useCameraStore();
const robot = useRobotStore();
const imgEl = ref(null);
const liveFlag = ref(false);
const expanded = ref(false);
const snapMsg = ref("");

const CAM_URL = cameraBase();

// ★★ 只认**第一个**视图（= 面板自己所在的那个视图容器）。
//   props.views 里还带着 'live'（如 ['point','live']），若把数组里所有视图都当活跃，
//   那么在「真实监控」页上，RealMonitor / PointExecView / ProgramExecView 三个实例
//   会**同时**认为自己在活跃 —— 于是 3 条 /status 轮询 + 3 条 MJPEG 连接一起打到
//   相机服务，浏览器还要同时解码 3 路视频。这正是现场"摄像头死机 / 实时捕捉不到"
//   的直接原因之一：面板越多，服务端与前端越先被自己压死。
const OWN_VIEW = props.views[0];
const isActive = () => robot.activeView === OWN_VIEW;

// ---------- 拖拽 / 缩放 ----------
const pos = reactive({ x: 12, y: 12 });
const size = reactive({ w: 300, h: 260 });
const LS_KEY = "efort.campanel";

function loadLayout() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_KEY) || "null");
    if (s && typeof s.x === "number") {
      pos.x = s.x; pos.y = s.y; size.w = s.w; size.h = s.h; return;
    }
  } catch (e) { /* 忽略 */ }
  pos.x = 12; pos.y = 12;
}
function saveLayout() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      x: Math.round(pos.x), y: Math.round(pos.y),
      w: Math.round(size.w), h: Math.round(size.h),
    }));
  } catch (e) { /* 忽略 */ }
}

let drag = null;
function onHeadDown(e) {
  if (e.target.closest(".cam-toggle")) return;
  drag = { sx: e.clientX, sy: e.clientY, ox: pos.x, oy: pos.y, moved: false };
  window.addEventListener("pointermove", onDragMove);
  window.addEventListener("pointerup", onDragUp);
}
function onDragMove(e) {
  if (!drag) return;
  const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
  if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.moved = true;
  pos.x = Math.max(0, Math.min(window.innerWidth - 80, drag.ox + dx));
  pos.y = Math.max(0, Math.min(window.innerHeight - 44, drag.oy + dy));
}
function onDragUp() {
  window.removeEventListener("pointermove", onDragMove);
  window.removeEventListener("pointerup", onDragUp);
  if (drag && drag.moved) saveLayout();
  drag = null;
}

let rz = null;
function onResizeDown(e) {
  e.stopPropagation();
  rz = { sx: e.clientX, sy: e.clientY, ow: size.w, oh: size.h };
  window.addEventListener("pointermove", onResizeMove);
  window.addEventListener("pointerup", onResizeUp);
}
function onResizeMove(e) {
  if (!rz) return;
  const dx = e.clientX - rz.sx, dy = e.clientY - rz.sy;
  size.w = Math.max(240, Math.min(760, rz.ow + dx));
  size.h = Math.max(160, Math.min(620, rz.oh + dy));
}
function onResizeUp() {
  window.removeEventListener("pointermove", onResizeMove);
  window.removeEventListener("pointerup", onResizeUp);
  saveLayout();
  rz = null;
}

// ---------- 状态计算 ----------
const device = computed(() => {
  const d = cam.status && cam.status.device;
  return d && d.model ? `${d.model} · ${d.ip}` : "—";
});

const dotCls = computed(() => {
  if (cam.serviceDown) return "err";
  const s = cam.status;
  if (!s) return "";
  if (s.opened && !s.closing) return "ok";
  if (s.opening || s.closing) return "warn";
  return "";
});

const stateText = computed(() => {
  if (cam.serviceDown) return "服务不可用";
  const s = cam.status;
  if (!s) return "未连接";
  if (s.opening) return "打开中…";
  if (s.closing) return "关闭中…";
  if (s.opened) return streamStalled.value ? "画面中断" : "已开启";
  return "已关闭";
});

const placeholderTitle = computed(() => {
  if (cam.serviceDown) return "相机服务不可用";
  const s = cam.status;
  if (s && s.opening) return "正在打开相机…";
  if (s && s.closing) return "正在关闭…";
  if (s && s.opened) return streamStalled.value ? "画面中断，正在自动重连…" : "等待画面…";
  return "相机未开启";
});

const placeholderSub = computed(() => {
  if (cam.serviceDown) return "请启动 camera/camera_service.py 后点「重试」";
  if (streamStalled.value) return "MJPEG 连接已断开，正在重新建连";
  return "";
});

// 颜色检测状态
const colorStatus = computed(() => {
  const v = cam.status?.vision;
  if (!v || !v.supported) return { text: "不支持", ok: false };
  if (v.enabled) return { text: "运行中", ok: true };
  return { text: "已关闭", ok: false };
});

// ---------- 轮询 ----------
// ★ 与旧版的关键差别：**永不主动停**（只要本面板还是活跃视图）。
//   旧版遇到错误就 return，服务恢复后界面永远停在"未连接" —— 必须手点刷新。
let pollTimer = null;
function stopPoll() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}
async function poll() {
  pollTimer = null;
  if (!isActive()) return;
  const busy = await cam.refresh();
  let delay;
  if (cam.serviceDown) delay = 3000;        // 服务不可达：慢速重试，恢复后自动接上
  else if (busy) delay = 400;               // 开关过渡中：加速轮询
  else if (cam.opened) delay = 1000;        // 正常取流：1s 一次状态
  else delay = 3000;                        // 服务在线但相机没开：慢速保活
  if (document.hidden) delay = Math.max(delay, 5000);   // 后台标签页降频
  pollTimer = setTimeout(poll, delay);
}
function startPoll(delay = 300) {
  stopPoll();
  pollTimer = setTimeout(poll, delay);
}

// ---------- MJPEG 流 ----------
// 断流看门狗：MJPEG 是长连接，被服务端/网络悄悄掐断时浏览器**既不报错也不重连**，
// 画面就那么定住（"实时捕捉不到"）。所以必须自己盯着"最后一帧的时间"。
const STREAM_STALL_MS = 15000;           // ★ 15s 超时：工业相机偶尔卡顿，太短会误触发
const RECONNECT_GRACE_MS = 8000;         // ★ 重连后 8s 稳定期：新连接第一帧可能慢，别急着又断
let lastFrameAt = 0;
let lastReconnectAt = 0;                 // ★ 记录上次重连时间，稳定期内不看门狗
let streamRetryMs = 1000;
let streamRetryTimer = null;
const streamStalled = ref(false);

function onFrameLoad() {
  lastFrameAt = Date.now();
  streamRetryMs = 1000;                    // 有帧回来 → 退避复位
  if (streamStalled.value) streamStalled.value = false;
}
function onFrameError() {
  // img 取流失败（服务端断开 / 404）：走与"卡住"同一套重连逻辑
  lastFrameAt = 0;
}

function connectStream() {
  // 页面隐藏时不断 MJPEG：隐藏页仍会持续解码视频流（GPU/网络大头）
  const want = cam.canStream && isActive() && !document.hidden;
  if (want) {
    if (!liveFlag.value && imgEl.value) {
      lastFrameAt = Date.now();
      lastReconnectAt = Date.now();        // ★ 记录重连时间，进入稳定期
      streamStalled.value = false;
      imgEl.value.src = CAM_URL + "/stream?t=" + Date.now();
      liveFlag.value = true;
    }
  } else if (liveFlag.value) {
    if (imgEl.value) imgEl.value.removeAttribute("src");
    liveFlag.value = false;
    streamStalled.value = false;
  }
}

function checkStall() {
  if (document.hidden || !isActive()) return;
  // ★ 该取流却没在取流 → 立刻自愈建连。
  //   以前这里遇到 !liveFlag 直接 return，建连**只**由 cam.status 变化触发；
  //   只要那一次触发落空（imgEl 尚未就绪、正好在切换视图、状态恰好没变），
  //   画面就再也不会出来 —— 表现就是"一直显示正在打开相机，刷新页面才见画面"。
  //   现在看门狗每 2.5s 兜一次，最迟 2.5s 自动接上，不必再刷新。
  if (!liveFlag.value) {
    if (cam.canStream) connectStream();
    return;
  }
  // ★ 重连后稳定期：新连接刚建立，第一帧可能慢（浏览器建连 + 服务端编码），
  //   这时不看门狗，避免"刚连上又断"的闪来闪去循环。
  if (Date.now() - lastReconnectAt < RECONNECT_GRACE_MS) return;
  if (Date.now() - lastFrameAt < STREAM_STALL_MS) return;
  // 15 秒没有新帧 → 主动清 src 重新建连（不清的话浏览器永远等在那条死连接上）
  streamStalled.value = true;
  if (imgEl.value) imgEl.value.removeAttribute("src");
  liveFlag.value = false;
  const d = streamRetryMs;
  streamRetryMs = Math.min(streamRetryMs * 2, 8000);   // 指数退避，避免疯狂重连
  if (streamRetryTimer) clearTimeout(streamRetryTimer);
  streamRetryTimer = setTimeout(() => {
    streamRetryTimer = null;
    if (isActive() && !document.hidden && cam.canStream) connectStream();
  }, d);
}

watch(() => cam.status, connectStream);

// 页面隐藏 → 断流省资源；回到前台 → 重新拉流
function onVis() { connectStream(); }
document.addEventListener("visibilitychange", onVis);

let stallTimer = null;
onMounted(() => {
  loadLayout();
  poll();
  cam.loadDevices();  // 加载可用相机设备列表
  stallTimer = setInterval(checkStall, 2500);
});
onBeforeUnmount(() => {
  stopPoll();
  if (stallTimer) { clearInterval(stallTimer); stallTimer = null; }
  if (streamRetryTimer) { clearTimeout(streamRetryTimer); streamRetryTimer = null; }
  document.removeEventListener("visibilitychange", onVis);
});

watch(() => robot.activeView, (v) => {
  if (v === OWN_VIEW) {
    if (!drag && !rz) loadLayout();
    poll();
    connectStream();
  } else {
    stopPoll();
    if (liveFlag.value) {
      if (imgEl.value) imgEl.value.removeAttribute("src");
      liveFlag.value = false;
    }
  }
});

// ---------- 操作 ----------
const acting = ref(false);
async function onToggle() {
  if (acting.value) return;
  acting.value = true;
  try {
    await cam.toggle();
  } catch (e) {
    cam.error = e.message;
  }
  acting.value = false;
  startPoll();
}

const switching = ref(false);
async function onDeviceChange(e) {
  const serial = e.target.value;
  const device = cam.devices.find(d => d.serial === serial);
  if (!device) return;
  switching.value = true;
  try {
    const r = await cam.switchDevice(device.idx, device.cam_type);
    if (!r.ok) {
      cam.error = r.error || "切换失败";
    }
  } catch (e) {
    cam.error = e.message;
  }
  switching.value = false;
}

async function onSnap() {
  const r = await cam.snap();
  snapMsg.value = r.ok ? ("已保存: " + r.path) : ("保存失败: " + (r.error || "无画面"));
  setTimeout(() => { snapMsg.value = ""; }, 4000);
}

const reconnecting = ref(false);
async function reconnectCam() {
  if (reconnecting.value) return;
  reconnecting.value = true;
  try { await cam.reconnect(); } catch (e) { /* cam.error 已由 store 写入 */ }
  reconnecting.value = false;
  startPoll();
}

/** 服务不可用时的"重试"：直接重拉一次状态，通了就自动恢复轮询与取流。 */
async function retryService() {
  await cam.refresh();
  if (!cam.serviceDown) { startPoll(100); connectStream(); }
}
</script>

<template>
  <div class="cam-panel" :class="{ collapsed: !expanded, down: cam.serviceDown }"
       :style="{ left: pos.x + 'px', top: pos.y + 'px', width: size.w + 'px' }">
    <!-- 标题栏 -->
    <div class="cam-head" @pointerdown="onHeadDown">
      <Icon name="grip" :size="14" class="cam-grip" />
      <Icon name="camera" :size="15" class="cam-ico" />
      <span class="cam-title">摄像头</span>
      <span class="dot" :class="dotCls"></span>
      <span class="cam-state">{{ stateText }}</span>
      <span v-if="cam.status?.fps" class="cam-fps">{{ cam.status.fps }} fps</span>
      <button class="cam-toggle" type="button" :title="expanded ? '收起' : '展开'"
              @click="expanded = !expanded">
        <Icon :name="expanded ? 'chevronDown' : 'chevronRight'" :size="16" />
      </button>
    </div>

    <!-- 服务不可用：与"相机没开"是完全不同的两件事，必须区分显示 -->
    <div v-if="cam.serviceDown" class="svc-down">
      <Icon name="alert" :size="14" />
      <span class="sd-text">相机服务不可用</span>
      <button class="sd-retry" type="button" @click="retryService">
        <Icon name="refresh" :size="12" /> 重试
      </button>
    </div>

    <!-- 画面区域 -->
    <div class="cam-screen" :style="{ height: (expanded ? size.h : 200) + 'px' }">
      <img v-show="liveFlag" ref="imgEl" class="cam-img" alt=""
           @load="onFrameLoad" @error="onFrameError" />
      <div v-if="!liveFlag" class="cam-placeholder">
        <Icon :name="cam.serviceDown ? 'alert' : 'camera'" :size="32" class="cam-ph-icon" />
        <span class="cam-ph-title">{{ placeholderTitle }}</span>
        <span v-if="placeholderSub" class="cam-ph-sub">{{ placeholderSub }}</span>
      </div>
      <!-- 画面卡住时的角标：明确告诉用户"在自动重连"，而不是让人以为死机了 -->
      <div v-if="liveFlag && streamStalled" class="cam-stall">
        <Icon name="refresh" :size="12" class="spin" /> 正在重连画面…
      </div>
    </div>

    <!-- 主控制区域（收起时也显示） -->
    <div class="cam-controls">
      <!-- 设备选择下拉 -->
      <div class="device-select" v-if="cam.devices.length > 0">
        <label class="device-label">
          <Icon name="camera" :size="13" />
          <span>设备</span>
        </label>
        <select class="device-dropdown" :value="cam.currentDevice?.serial"
                :disabled="cam.busy || cam.opened || cam.serviceDown"
                @change="onDeviceChange">
          <option v-for="d in cam.devices" :key="d.serial" :value="d.serial">
            {{ d.model }} ({{ d.type }} {{ d.serial }})
          </option>
        </select>
      </div>

      <!-- 一键启停 -->
      <button class="power-btn" :class="{ on: cam.opened, busy: cam.busy || acting }"
              :disabled="cam.busy || acting || cam.serviceDown" @click="onToggle">
        <Icon name="power" :size="18" />
        <span>{{ cam.busy ? '处理中…' : (cam.opened ? '关闭相机' : '开启相机') }}</span>
      </button>

      <!-- 快速状态栏 -->
      <div class="quick-stats">
        <div class="stat-item">
          <span class="stat-label">颜色检测</span>
          <span class="stat-value" :class="{ active: cam.colorEnabled }">{{ colorStatus.text }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">识别次数</span>
          <span class="stat-value">{{ cam.colorCount }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">画面</span>
          <span class="stat-value" :class="{ active: cam.opened && !streamStalled }">
            {{ cam.opened ? (streamStalled ? '中断' : '正常') : '—' }}
          </span>
        </div>
      </div>
    </div>

    <!-- 展开后的详细控制 -->
    <div v-show="expanded" class="cam-body">
      <!-- 灰度相机提示：无彩色信息，无法做颜色识别 -->
      <div class="color-unavail" v-if="cam.colorSupported && !cam.colorCapable">
        <span class="dot"></span> 当前相机为<strong>灰度相机</strong>，画面无彩色信息，无法做红/绿/蓝识别。请切换到<strong>彩色相机</strong>（USB 彩色 / 海康 GC 系列）。
      </div>

      <!-- 颜色分拣开关 -->
      <div class="control-row" v-if="cam.colorSupported && cam.colorCapable">
        <label class="control-label">颜色分拣</label>
        <label class="switch">
          <input type="checkbox" :checked="cam.colorEnabled" :disabled="cam.serviceDown"
                 @change="cam.setColor($event.target.checked)" />
          <span class="slider"></span>
        </label>
      </div>

      <!-- 三色识别目标 + 置信度阈值 -->
      <div class="control-row conf-row" v-if="cam.colorSupported && cam.colorCapable">
        <label class="control-label">
          三色置信度&nbsp;≥&nbsp;<b>{{ (cam.colorConf * 100).toFixed(0) }}%</b>
        </label>
        <input class="conf-slider" type="range" min="15" max="95" step="5"
               :value="(cam.colorConf * 100).toFixed(0)"
               :disabled="cam.serviceDown" title="confidence: conf 低于此值的目标不画框/不记录"
               @change="cam.setColorConf($event.target.value / 100)" />
      </div>
      <div class="target-row" v-if="cam.colorSupported && cam.colorCapable && cam.colorTargets.length">
        <span v-for="t in cam.colorTargets" :key="t" class="target-chip"
              :class="'chip-' + t">{{ t }}</span>
      </div>

      <!-- 操作按钮 -->
      <div class="action-btns">
        <button @click="onSnap" :disabled="!cam.opened">
          <Icon name="snapshot" :size="14" /> 截图
        </button>
        <button @click="reconnectCam" :disabled="reconnecting || cam.busy">
          <Icon name="refresh" :size="14" /> 重连
        </button>
      </div>

      <!-- 状态信息 -->
      <div class="info-grid">
        <span class="info-label">设备</span>
        <span class="info-value">{{ device }}</span>
        <span class="info-label">分辨率</span>
        <span class="info-value">{{ cam.status?.resolution || "—" }}</span>
        <span class="info-label">分割</span>
        <span class="info-value">{{ cam.status?.vision?.seg || "—" }}</span>
      </div>

      <!-- 消息提示 -->
      <div v-if="cam.error" class="error-msg">{{ cam.error }}</div>
      <div v-else-if="cam.deviceError" class="error-msg">{{ cam.deviceError }}</div>
      <div v-if="snapMsg" class="info-msg">{{ snapMsg }}</div>
    </div>

    <!-- 缩放手柄 -->
    <div class="cam-resize" @pointerdown="onResizeDown" title="拖动缩放">
      <Icon name="expand" :size="13" />
    </div>
  </div>
</template>

<style scoped>
/* ★ z-index 30：面板浮在 3D 视口上，必须高于关节 HUD(10)、状态片(14)、
   执行 HUD(12) —— 否则拖动/缩放面板时会被这些浮层"咬"掉一块。 */
.cam-panel {
  position: absolute; z-index: 30;
  background: var(--glass-solid);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: var(--shadow-hud);
  backdrop-filter: blur(12px) saturate(140%);
  -webkit-backdrop-filter: blur(12px) saturate(140%);
  overflow: hidden;
}
.cam-panel.down { border-color: var(--err-line); }

.cam-head {
  display: flex; align-items: center; gap: 7px;
  padding: 8px 10px;
  cursor: move; user-select: none; font-size: 13px;
  background: linear-gradient(180deg, var(--veil), rgba(255,255,255,0));
  border-bottom: 1px solid var(--line);
}
.cam-grip { color: var(--muted); cursor: move; opacity: .8; }
.cam-ico { color: var(--accent); }
.cam-title { font-weight: 600; }
.cam-state { color: var(--muted); margin-left: auto; font-size: 12px; }
.cam-fps { color: var(--ok); font-size: 11px; font-variant-numeric: tabular-nums; }
.cam-toggle {
  display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; margin-left: 6px; padding: 0;
  border: none; border-radius: 6px;
  background: transparent; color: var(--muted); cursor: pointer;
}
.cam-toggle:hover { background: var(--veil2); color: var(--txt); }

.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex: none; }
.dot.ok { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
.dot.warn { background: var(--warn); box-shadow: 0 0 6px var(--warn); }
.dot.err { background: var(--err); box-shadow: 0 0 6px var(--err); }

/* ---- 服务不可用横幅 ---- */
.svc-down {
  display: flex; align-items: center; gap: 7px;
  padding: 6px 10px; font-size: 12px;
  color: var(--err); background: var(--err-soft);
  border-bottom: 1px solid var(--err-line);
}
.svc-down svg { flex: none; }
.sd-text { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.sd-retry {
  flex: none; width: auto; display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; font-size: 11px; border-radius: 5px;
  color: var(--txt); background: var(--panel); border: 1px solid var(--line);
  cursor: pointer;
}
.sd-retry:hover { border-color: var(--accent); color: var(--accent); }

.cam-screen {
  position: relative; width: 100%; background: var(--screen);
  border-bottom: 1px solid var(--line); overflow: hidden;
  transition: height .18s ease;
}
.cam-img { width: 100%; height: 100%; object-fit: contain; background: var(--screen); display: block; }
.cam-placeholder {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 6px;
  color: var(--muted); pointer-events: none; text-align: center; padding: 0 14px;
}
.cam-ph-icon { opacity: .5; }
.cam-ph-title { font-size: 13px; color: var(--txt); }
.cam-ph-sub { font-size: 11px; line-height: 1.5; }
.cam-stall {
  position: absolute; left: 8px; bottom: 8px;
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 8px; border-radius: 5px; font-size: 11px;
  color: var(--warn); background: var(--glass);
  border: 1px solid var(--warn-line);
}

/* ---- 主控制区域 ---- */
.cam-controls {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  background: var(--scrim);
}

/* ---- 设备选择下拉 ---- */
.device-select {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 10px; padding: 8px 10px;
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 6px;
}
.device-label {
  display: flex; align-items: center; gap: 5px;
  font-size: 12px; color: var(--muted); white-space: nowrap;
}
.device-dropdown {
  flex: 1; min-width: 0;
  padding: 5px 8px; font-size: 12px;
  background: var(--glass); color: var(--txt);
  border: 1px solid var(--line); border-radius: 4px;
  cursor: pointer; outline: none;
}
.device-dropdown:hover:not(:disabled) { border-color: var(--accent); }
.device-dropdown:disabled { opacity: .5; cursor: not-allowed; }
.device-dropdown option { background: var(--panel); color: var(--txt); }

.power-btn {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  width: 100%; padding: 10px 16px;
  font-size: 14px; font-weight: 600;
  border: none; border-radius: 8px;
  cursor: pointer; transition: all .15s ease;
  background: var(--panel2); color: var(--txt);
}
.power-btn:hover:not(:disabled) { background: var(--panel3, var(--panel)); }
.power-btn:disabled { opacity: .5; cursor: not-allowed; }
.power-btn.on {
  background: linear-gradient(135deg, var(--ok), var(--c-green));
  color: var(--ink);
  box-shadow: var(--shadow);
}
.power-btn.on:hover:not(:disabled) {
  background: linear-gradient(135deg, var(--c-green), var(--ok-btn));
}
.power-btn.busy {
  background: var(--warn); color: var(--ink);
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .7; } }

.quick-stats {
  display: flex; justify-content: space-around;
  margin-top: 10px; padding-top: 10px;
  border-top: 1px solid var(--line);
}
.stat-item {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
}
.stat-label { font-size: 10px; color: var(--muted); }
.stat-value { font-size: 12px; font-weight: 500; font-variant-numeric: tabular-nums; }
.stat-value.active { color: var(--ok); }

/* ---- 展开后的详细控制 ---- */
.cam-body {
  padding: 12px;
  display: flex; flex-direction: column; gap: 12px;
}

.control-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px;
}
.control-label {
  font-size: 12px; color: var(--muted);
  white-space: nowrap;
}

/* 开关样式 */
.switch {
  position: relative; display: inline-block;
  width: 40px; height: 22px;
}
.switch input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute; cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background: var(--panel2);
  border-radius: 22px;
  transition: .2s;
}
.slider:before {
  position: absolute; content: "";
  height: 16px; width: 16px;
  left: 3px; bottom: 3px;
  background: var(--muted);
  border-radius: 50%;
  transition: .2s;
}
input:checked + .slider { background: var(--accent); }
input:checked + .slider:before {
  transform: translateX(18px);
  background: var(--ink);
}
input:disabled + .slider { opacity: .45; cursor: not-allowed; }

/* 灰度相机提示 */
.color-unavail {
  display: flex; align-items: center; gap: 7px;
  font-size: 12px; line-height: 1.5; color: #b3872a;
  background: rgba(214, 158, 46, 0.10); border: 1px solid rgba(214, 158, 46, 0.28);
  border-radius: 8px; padding: 8px 10px; margin-bottom: 8px;
}
.color-unavail .dot { width: 8px; height: 8px; border-radius: 50%; background: #d69e2e; flex: none; }
.color-unavail strong { color: #e8b347; }

/* 三色置信度滑杆 */
.conf-row { padding-top: 4px; }
.conf-slider {
  width: 128px; flex: none; accent-color: var(--accent); cursor: pointer;
}
.conf-slider:disabled { opacity: .45; cursor: not-allowed; }

/* 三色目标 chips */
.target-row { display: flex; gap: 6px; margin-top: 6px; }
.target-chip {
  font-size: 11px; line-height: 1; padding: 4px 10px; border-radius: 10px;
  color: #fff; opacity: .92;
}
.chip-红 { background: #e5404a; }
.chip-绿 { background: #2f9e57; }
.chip-蓝 { background: #2f6fed; }

.action-btns {
  display: flex; gap: 6px;
}
.action-btns button {
  flex: 1; display: flex; align-items: center; justify-content: center; gap: 4px;
  padding: 6px 8px;
  font-size: 11px;
  background: var(--panel); color: var(--txt);
  border: 1px solid var(--line); border-radius: 6px;
  cursor: pointer;
}
.action-btns button:hover:not(:disabled) {
  background: var(--panel2);
  border-color: var(--accent);
}
.action-btns button:disabled {
  opacity: .4; cursor: not-allowed;
}

.info-grid {
  display: grid; grid-template-columns: auto 1fr;
  gap: 4px 10px; font-size: 11px;
}
.info-label { color: var(--muted); }
.info-value { color: var(--txt); font-variant-numeric: tabular-nums; }

.error-msg {
  color: var(--err); font-size: 11px;
  padding: 6px 8px; line-height: 1.5;
  background: var(--err-soft);
  border-radius: 4px;
}
.info-msg {
  color: var(--muted); font-size: 11px;
  padding: 4px 8px;
  background: var(--panel);
  border-radius: 4px;
}

.cam-resize {
  position: absolute; right: 0; bottom: 0;
  width: 18px; height: 18px;
  display: flex; align-items: center; justify-content: center;
  color: var(--muted); cursor: nwse-resize; opacity: .7;
}
.cam-resize:hover { opacity: 1; color: var(--accent); }

.spin { animation: cam-spin .8s linear infinite; }
@keyframes cam-spin { to { transform: rotate(360deg); } }
</style>