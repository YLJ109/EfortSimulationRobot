<script setup>
// 根组件：Tab 栏 + 四视图（v-show 保持 3D 场景存活）+ 全局渲染循环 + 安全围栏报警。
//
// 三视图共用 MonitorLayout 模板 —— 同一个 scene、同一张 canvas、同一套模型。
import { onMounted, onBeforeUnmount, ref, computed } from "vue";
import { useRobotStore } from "./stores/robot.js";
import { useSafetyStore } from "./stores/safety.js";
import { switchView, renderFrame, resizeView, onSafety } from "./three/manager.js";
import RealMonitor from "./components/RealMonitor.vue";
import SimMonitor from "./components/SimMonitor.vue";
import RecordingView from "./components/RecordingView.vue";
import VisionView from "./components/VisionView.vue";

const robot = useRobotStore();
const safe = useSafetyStore();
const tabs = [
  { key: "live", label: "● 真实监控" },
  { key: "sim", label: "◆ 模拟仿真" },
  { key: "rec", label: "▤ 录制回放" },
  { key: "cam", label: "▣ 视觉检测" },
];

const RANK = { safe: 0, warn: 1, danger: 2, hit: 3 };

// 安全围栏状态（由 three 管理器每帧评估后回调）
const safety = ref({ state: "safe", ratio: 1, clearance: 0, zoneId: "", zoneName: "" });

// 报警来源区分：地面碰撞 vs 安全围栏
const isGround = computed(() => safety.value.zoneId === "ground");

const safetyText = computed(() => {
  if (isGround.value) {
    if (safety.value.state === "hit") return "碰撞报警！机器人已触及台面";
    if (safety.value.state === "danger") return "危险：机器人即将触及台面";
    if (safety.value.state === "warn") return "注意：机器人接近台面（离台面高度偏低）";
    return "安全：机器人离台面高度正常";
  }
  if (safety.value.state === "hit") return "碰撞报警！机器人已触及安全围栏";
  if (safety.value.state === "danger") return "危险：即将碰撞安全围栏（余量 < 10%）";
  return "注意：接近安全围栏（余量 < 30%）";
});
const safetySub = computed(() => {
  const c = safety.value.clearance;
  if (isGround.value) {
    return c > 0 ? `最低点离台面 ${(c * 1000).toFixed(0)} mm` : "已触台面，请立即停止";
  }
  const zone = safety.value.zoneName ? ` · ${safety.value.zoneName}` : "";
  return (c > 0 ? `剩约 ${(c * 1000).toFixed(0)} mm` : "已越界，请立即停止") + zone;
});
const safetyLabel = computed(() => ({
  safe: "安全", warn: "接近", danger: "危险", hit: "碰撞",
}[safety.value.state] || "安全"));
const safetyScope = computed(() => (isGround.value ? "地面" : "安全围栏"));
const chipTitle = computed(() =>
  isGround.value ? "地面碰撞：机器人最低点离台面的高度" : "安全围栏：距围栏内表面的最小余量");

// 报警条：按配置的 banner_min 决定最低触发级别
const showBanner = computed(() => {
  const a = safe.config && safe.config.alarm;
  if (!a || !a.banner) return false;
  const min = RANK[a.banner_min] ?? RANK.danger;
  return RANK[safety.value.state] >= min && RANK[safety.value.state] >= RANK.warn;
});
const showChip = computed(() => !safe.config || !safe.config.alarm || safe.config.alarm.chip !== false);

function setView(v) {
  if (v === robot.activeView) return;
  robot.setActiveView(v);
  if (v !== "cam") switchView(v); // cam 视图用 <img>，无需 3D canvas
}

let rafId = null;
function loop() {
  rafId = requestAnimationFrame(loop);
  if (robot.activeView !== "cam") renderFrame();
}

function onResize() {
  if (robot.activeView !== "cam") resizeView();
}

onMounted(async () => {
  onSafety((s) => { safety.value = s; safe.ingest(s); });
  await robot.loadMeta();  // 初始化机器人模型（子组件 onMounted 已登记容器）
  robot.connect();         // 建立 WebSocket
  await safe.load();       // 拉取安全围栏配置并应用到 3D
  switchView("live");      // canvas 挂到 live 视图
  loop();
  window.addEventListener("resize", onResize);
});

onBeforeUnmount(() => {
  if (rafId) cancelAnimationFrame(rafId);
  window.removeEventListener("resize", onResize);
});
</script>

<template>
  <div id="app">
    <div id="tabs">
      <button v-for="t in tabs" :key="t.key" class="tab"
              :class="{ active: robot.activeView === t.key }" @click="setView(t.key)">
        {{ t.label }}
      </button>
    </div>
    <div v-if="showBanner" id="safety-banner" :class="safety.state">
      <span>{{ safetyText }}</span>
      <span class="sa-sub">{{ safetySub }}</span>
    </div>
    <div v-if="showChip" id="safety-chip" :class="safety.state" :title="chipTitle">
      <span class="sc-dot"></span>
      <span>{{ safetyScope }} · {{ safetyLabel }}</span>
      <span class="sc-num">{{ safetySub }}</span>
    </div>
    <div id="main">
      <RealMonitor v-show="robot.activeView === 'live'" />
      <SimMonitor v-show="robot.activeView === 'sim'" />
      <RecordingView v-show="robot.activeView === 'rec'" />
      <VisionView v-show="robot.activeView === 'cam'" />
    </div>
  </div>
</template>
