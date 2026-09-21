<script setup>
// 三视图统一模板：左侧 3D 视口（+ HUD 插槽） + 右侧栏（安全调试面板 + 业务卡片）。
//
// 真实监控 / 模拟仿真 / 录制回放 全部套这个壳 —— 同一个 scene、同一张 canvas，
// 切换视图只是把 canvas 搬到对应的 .view 容器里，模型与视角原地不动。
import { ref, onMounted } from "vue";
import { registerView } from "../three/manager.js";
import SafetyPanel from "./SafetyPanel.vue";

const props = defineProps({
  view: { type: String, required: true },   // live | sim | rec
});

const viewEl = ref(null);
onMounted(() => registerView(props.view, viewEl.value));
</script>

<template>
  <div class="monitor">
    <div class="view" ref="viewEl">
      <slot name="hud" />
      <div class="hud-tip">鼠标左键旋转 · 滚轮缩放 · 右键平移</div>
    </div>
    <div class="side">
      <SafetyPanel />
      <slot />
    </div>
  </div>
</template>
