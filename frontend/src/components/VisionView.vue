<script setup>
// 视觉检测视图：海康相机 MJPEG 流 + YOLO 目标检测 + 截图。
import { ref, computed, onMounted, onBeforeUnmount, watch } from "vue";
import { useCameraStore } from "../stores/camera.js";
import { useRobotStore } from "../stores/robot.js";
import { cameraBase } from "../config.js";

const cam = useCameraStore();
const robot = useRobotStore();
const imgEl = ref(null);
const liveFlag = ref(false);
const snapMsg = ref("保存到 camera/captures/");

const CAM_URL = cameraBase();

const device = computed(() => {
  const d = cam.status && cam.status.device;
  return d && d.model ? `${d.model} · ${d.ip}` : "—";
});
const detCount = computed(() =>
  cam.status && cam.status.ai_enabled ? (cam.status.det_count ?? 0) : "—"
);
const detMs = computed(() =>
  cam.status && cam.status.ai_enabled && cam.status.det_ms ? cam.status.det_ms + " ms" : "—"
);
const placeholderTitle = computed(() => {
  const s = cam.status;
  if (s && s.opening) return "正在打开相机…";
  if (s && s.closing) return "正在关闭…";
  if (s && s.opened) return "等待画面…";
  return "相机未开启";
});
const placeholderSub = computed(() => {
  const s = cam.status;
  if (s && (s.opening || s.closing)) return "";
  return "点击右侧「打开相机」开始画面采集；若刚切换过网线，请先点「重连相机」";
});

// 轮询策略（按需，避免相机服务未启动时控制台刷屏 ERR_CONNECTION_REFUSED）：
//   - 进入视图 / 点按钮: 请求一次
//   - 相机服务不可达: 停止轮询（用户点「重连相机」手动重试）
//   - 相机已打开/过渡中: 轮询刷新 fps / 检测数
//   - 相机未打开: 停止轮询
let pollTimer = null;

function stopPoll() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

async function poll() {
  pollTimer = null;
  if (robot.activeView !== "cam") return;
  const busy = await cam.refresh();
  if (cam.error) return;                 // 服务不可达 -> 停止（不刷屏）
  if (!cam.opened && !busy) return;      // 相机未打开 -> 停止（按需）
  pollTimer = setTimeout(poll, busy ? 400 : 1000);
}

function startPoll(delay = 300) {
  stopPoll();
  pollTimer = setTimeout(poll, delay);
}

onMounted(() => { poll(); });            // 进入时只请求一次
onBeforeUnmount(() => stopPoll());

async function openCam() {
  await cam.open();
  startPoll();                           // 打开后开始轮询状态
}

async function closeCam() {
  await cam.close();
  stopPoll();
}

// MJPEG 流：进入相机视图 / 打开相机时连接，离开时断开省资源
watch(() => cam.status, (s) => {
  if (!s) return;
  if (s.opened && !s.closing) {
    if (!liveFlag.value && imgEl.value) {
      imgEl.value.src = CAM_URL + "/stream?t=" + Date.now();
      liveFlag.value = true;
    }
  } else if (liveFlag.value) {
    if (imgEl.value) imgEl.value.removeAttribute("src");
    liveFlag.value = false;
  }
});

watch(() => robot.activeView, (v) => {
  if (v === "cam") {
    poll();                              // 进入视图: 请求一次（相机已开则自动续轮询）
  } else {
    stopPoll();                          // 离开视图: 停止轮询
    if (liveFlag.value) {
      if (imgEl.value) imgEl.value.removeAttribute("src");
      liveFlag.value = false;
    }
  }
});

async function onSnap() {
  const r = await cam.snap();
  snapMsg.value = r.ok ? ("已保存: " + r.path) : ("保存失败: " + (r.error || "无画面"));
}

// 手动重连相机（网线在摄像头/机器人间切换后，点此触发，无需重启）
const reconnecting = ref(false);
async function reconnectCam() {
  if (reconnecting.value) return;
  reconnecting.value = true;
  await cam.reconnect();
  reconnecting.value = false;
  startPoll();                           // 重连后重新开始轮询
}

// 卸载 YOLO 模型，释放 torch 常驻内存（可达数 GB）
const unloading = ref(false);
const unloadMsg = ref("");
async function unloadModel() {
  if (unloading.value) return;
  unloading.value = true;
  unloadMsg.value = "正在释放…";
  const r = await cam.unload();
  unloadMsg.value = r && r.ok ? "已释放模型内存" : "释放失败";
  unloading.value = false;
}
</script>

<template>
  <div class="monitor">
    <div class="view">
      <img v-show="liveFlag" ref="imgEl" class="cam-img" alt="" />
      <div v-if="!liveFlag" class="cam-placeholder">
        <svg class="ph-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round">
          <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
          <circle cx="12" cy="13" r="4"/>
        </svg>
        <div class="ph-title">{{ placeholderTitle }}</div>
        <div v-if="placeholderSub" class="ph-sub">{{ placeholderSub }}</div>
      </div>
    </div>
    <div class="side">
      <div class="card">
        <h3>相机</h3>
        <div class="btns">
          <button class="primary" :disabled="cam.opened || cam.busy" @click="openCam">打开相机</button>
          <button :disabled="(!cam.opened && !cam.status?.ai_loaded) || cam.busy" @click="closeCam">关闭相机</button>
        </div>
        <div class="btns" style="margin-top:8px">
          <button :disabled="reconnecting || cam.busy" @click="reconnectCam">↻ 重连相机</button>
        </div>
        <div class="row" style="margin-top:8px"><span class="k">设备</span><span class="v">{{ device }}</span></div>
        <div class="row"><span class="k">分辨率</span><span class="v">{{ cam.status?.resolution || '—' }}</span></div>
        <div class="row"><span class="k">帧率</span><span class="v">{{ cam.status?.fps ? cam.status.fps + ' fps' : '—' }}</span></div>
        <div v-if="cam.error || cam.status?.error" class="calib">{{ cam.error || cam.status.error }}</div>
      </div>
      <div class="card">
        <h3>AI 目标检测 (YOLO)</h3>
        <div class="status" style="margin-bottom:8px">
          <input type="checkbox" id="cam-ai" :checked="!!cam.status?.ai_enabled"
                 @change="cam.setAI($event.target.checked)" />
          <label for="cam-ai">启用检测</label>
          <span v-if="cam.status?.ai_loading" class="small" style="margin-left:auto">加载中…</span>
          <span v-else-if="cam.status?.ai_loaded" class="tag sim" style="margin-left:auto">模型已常驻</span>
        </div>
        <div class="row"><span class="k">模型</span>
          <select :value="cam.status?.ai_model" @change="cam.setModel($event.target.value)">
            <option v-for="m in (cam.status?.models || [])" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
        <div class="row"><span class="k">置信度</span>
          <input type="number" min="0.05" max="0.95" step="0.05" value="0.25"
                 @change="cam.setConf(parseFloat($event.target.value))" />
        </div>
        <div class="row"><span class="k">推理尺寸</span>
          <input type="number" min="320" max="1280" step="32" value="640"
                 @change="cam.setImgsz(parseInt($event.target.value, 10))" />
        </div>
        <div class="row" style="margin-top:8px"><span class="k">目标数</span><span class="v">{{ detCount }}</span></div>
        <div class="row"><span class="k">单帧耗时</span><span class="v">{{ detMs }}</span></div>
        <div class="btns" style="margin-top:8px">
          <button :disabled="!cam.status?.ai_loaded || cam.status?.ai_loading || unloading" @click="unloadModel">
            ⏏ 释放模型内存
          </button>
        </div>
        <div v-if="unloadMsg" class="small" style="margin-top:6px">{{ unloadMsg }}</div>
        <div class="small" style="margin-top:6px">
          启用检测会加载 YOLO(PyTorch) 模型，常驻约数 GB 内存；不用时点「释放模型内存」归还。
        </div>
      </div>
      <div class="card">
        <h3>截图</h3>
        <button @click="onSnap">保存当前帧</button>
        <div class="small" style="margin-top:6px">{{ snapMsg }}</div>
      </div>
    </div>
  </div>
</template>
