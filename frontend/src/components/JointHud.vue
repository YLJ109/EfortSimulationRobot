<script setup>
// 实时关节 HUD 卡片（真实监控左上角）—— 玻璃拟态 + 运动高亮 + 限位告警。
// 迁移自原 main.js 的 buildJointHud / updateJointHud / tickJointHud。
import { ref, watch, onMounted, onUnmounted } from "vue";
import { useRobotStore } from "../stores/robot.js";

const robot = useRobotStore();

const collapsed = ref(false);
const fpsText = ref("— Hz");
const srcText = ref("—");
const dotCls = ref("off");

const rows = ref(
  robot.axes.map((n) => ({ name: n, val: "0.0", bar: 0, cls: "" }))
);

const prev = [0, 0, 0, 0, 0, 0];
const lastMoveT = [0, 0, 0, 0, 0, 0];
let fpsCount = 0;
let fpsT0 = performance.now();
let tickTimer = null;

const MOVE_EPS = 0.05;
const MOVE_HOLD = 300;
const NEAR_DEG = 10.0;
const LIMIT_DEG = 1.0;

function barPercent(i, v) {
  const lim = robot.limits[i] || { min: -180, max: 180 };
  const span = lim.max - lim.min || 1;
  return Math.max(0, Math.min(100, ((v - lim.min) / span) * 100));
}

function update() {
  const q = robot.latestPose;
  if (!q) return;
  const now = performance.now();
  fpsCount++;
  if (now - fpsT0 >= 1000) {
    fpsText.value = fpsCount + " Hz";
    fpsCount = 0;
    fpsT0 = now;
  }
  srcText.value = robot.simulated ? "模拟" : "真实";
  dotCls.value = robot.simulated ? "warn" : "ok";
  for (let i = 0; i < 6; i++) {
    const v = q[i];
    const lim = robot.limits[i] || { min: -180, max: 180 };
    if (Math.abs(v - prev[i]) > MOVE_EPS) lastMoveT[i] = now;
    const moving = (now - lastMoveT[i]) < MOVE_HOLD;
    let cls = "";
    if (v >= lim.max - LIMIT_DEG || v <= lim.min + LIMIT_DEG) cls += "limit";
    else if (v >= lim.max - NEAR_DEG || v <= lim.min + NEAR_DEG) cls += "near";
    if (moving) cls += (cls ? " " : "") + "moving";
    rows.value[i].val = v.toFixed(1);
    rows.value[i].bar = barPercent(i, v);
    rows.value[i].cls = cls;
    prev[i] = v;
  }
}

function tick() {
  const now = performance.now();
  for (let i = 0; i < 6; i++) {
    if ((now - lastMoveT[i]) >= MOVE_HOLD && rows.value[i].cls.includes("moving")) {
      rows.value[i].cls = rows.value[i].cls.replace(/\s?moving/, "").trim();
    }
  }
}

watch(() => robot.latestPose, update);
watch(() => robot.connected, (ok) => {
  if (!ok) { dotCls.value = "off"; srcText.value = "断开"; }
});

onMounted(() => { tickTimer = setInterval(tick, 120); });
onUnmounted(() => { clearInterval(tickTimer); });
</script>

<template>
  <div class="hud-joints">
    <div class="hud-head">
      <span class="hud-dot" :class="dotCls"></span>
      <span class="hud-title">实时关节角</span>
      <span class="hud-src">{{ srcText }}</span>
      <span class="hud-fps">{{ fpsText }}</span>
      <button class="hud-toggle" :title="collapsed ? '展开' : '折叠'" @click="collapsed = !collapsed">
        {{ collapsed ? '+' : '−' }}
      </button>
    </div>
    <div class="hud-body" :class="{ collapsed }">
      <div v-for="r in rows" :key="r.name" class="hud-row" :class="r.cls">
        <span class="hj">{{ r.name }}</span>
        <span class="hbar"><i :style="{ width: r.bar + '%' }"></i></span>
        <span class="hv">{{ r.val }}</span>
      </div>
    </div>
  </div>
</template>
