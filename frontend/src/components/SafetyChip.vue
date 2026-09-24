<script setup>
// 安全围栏常驻状态片 —— 浮在 **3D 视口右下角**。
//
// ★ 位置为什么在 .view 里（由 MonitorLayout 渲染）而不是全局底栏：
//   用户要求"放在画面右下角，不要在底部"。放进视口容器后它会自动跟随
//   右栏的拖宽/折叠与窄屏堆叠，不需要任何 JS 去算右栏宽度。
//   代价：设置/关于/运维审计这些**没有 3D 视口**的数据页看不到状态片。
//
// ★ 数据来源：safety store 的 last（由 App.vue 的 onSafety 回调喂进来）。
//   不自己起定时器、不自己算围栏 —— 全站只有一份评估结果。
//   文案统一取 utils/safetyLabels.js，与顶部报警条共用同一套判据。
import { computed } from "vue";
import { useSafetyStore } from "../stores/safety.js";
import { safetyLabel, safetyScope, safetySub, chipTitle } from "../utils/safetyLabels.js";

const safe = useSafetyStore();

// 配置里可以整个关掉状态片（alarm.chip === false），与后端 schema 对齐。
const show = computed(() => !safe.config || !safe.config.alarm || safe.config.alarm.chip !== false);
const st = computed(() => safe.last || { state: "safe" });
</script>

<template>
  <div v-if="show" id="safety-chip" :class="st.state" :title="chipTitle(st)">
    <span class="sc-dot"></span>
    <span v-if="st.source === 'ghost'" class="sc-src">残影</span>
    <span>{{ safetyScope(st) }} · {{ safetyLabel(st) }}</span>
    <span class="sc-num">{{ safetySub(st) }}</span>
  </div>
</template>
