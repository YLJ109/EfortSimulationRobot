<script setup>
// 录制回放视图：播放/暂停/停止 + 录制库 CRUD + 导入导出。
import { ref, computed, onMounted, watch } from "vue";
import { useRecordingStore } from "../stores/recording.js";
import { useRobotStore } from "../stores/robot.js";
import MonitorLayout from "./MonitorLayout.vue";

const rec = useRecordingStore();
const robot = useRobotStore();
const fileInput = ref(null);

const recHud = computed(() => {
  if (!rec.player.frames || !rec.player.frames.length) return "未载入录制 — 请从右侧录制库选择";
  return rec.player.name ? `${rec.player.name} · ${rec.player.frames.length} 帧` : "未载入录制";
});

const canPlay = computed(() => rec.hasFrames && rec.player.state !== "playing");
const canPause = computed(() => rec.playing || rec.paused);
const pauseLabel = computed(() => (rec.paused ? "▶ 继续" : "⏸ 暂停"));

onMounted(() => {
  rec.refreshList();
});

// 切回录制回放时补一次姿态（单模型，切视图不会自动还原）
watch(() => robot.activeView, (v) => { if (v === "rec") rec.applyCurrent(); });

function playItem(item) {
  rec.loadDetail(item).then(() => {
    robot.setActiveView("rec");
    rec.play();
  });
}

async function loadItem(item) {
  await rec.loadDetail(item);
}

async function renameItem(item) {
  const name = prompt("新名称:", item.name);
  if (name === null || !name.trim()) return;
  await rec.rename(item, name.trim());
}

async function deleteItem(item) {
  if (!confirm(`确定删除「${item.name}」？`)) return;
  await rec.remove(item);
}

function fmtTime(s) {
  return (s || "").replace("T", " ").slice(0, 19);
}

async function onImport(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    if (!data || !Array.isArray(data.frames) || !data.frames.length) {
      alert("导入失败：文件缺少 frames 数据");
      e.target.value = "";
      return;
    }
    await rec.importJson({
      format: data.format || null,
      name: data.name || file.name.replace(/\.json$/i, ""),
      description: data.description || "",
      source: data.source || "sim",
      duration_ms: data.duration_ms || null,
      frames: data.frames,
    });
  } catch (err) {
    alert("导入失败：" + err.message);
  }
  e.target.value = "";
}
</script>

<template>
  <MonitorLayout view="rec">
    <template #hud>
      <div class="rec-hud">{{ recHud }}</div>
    </template>
    <div class="side-cards">
      <div class="card">
        <h3>回放控制</h3>
        <div class="btns">
          <button :disabled="!canPlay" @click="rec.play()">▶ 播放</button>
          <button :disabled="!canPause" @click="rec.pauseToggle()">{{ pauseLabel }}</button>
          <button :disabled="!canPause" @click="rec.stopPlayback()">⏹ 停止</button>
        </div>
        <div class="row" style="margin-top:8px">
          <span class="k">进度</span><span class="v">{{ rec.player.progress || '— / —' }}</span>
        </div>
        <div class="small">{{ rec.player.name || "未载入录制" }}</div>
      </div>
      <div class="card">
        <h3>录制库</h3>
        <div class="btns" style="margin-bottom:8px">
          <button @click="rec.refreshList()">↻ 刷新</button>
          <button @click="fileInput.click()">⬆ 导入</button>
        </div>
        <input type="file" ref="fileInput" accept=".json,application/json" class="hidden" @change="onImport" />
        <div class="rec-list">
          <div v-if="!rec.list.length" class="rec-empty">暂无录制</div>
          <div v-for="item in rec.list" :key="item.id" class="rec-item">
            <div class="rec-head">
              <span class="rec-name" :title="item.name">{{ item.name }}</span>
              <span class="tag" :class="item.source === 'real' ? 'real' : 'sim'">{{ item.source === 'real' ? '真实' : '模拟' }}</span>
            </div>
            <div class="rec-meta">{{ item.frames_count }} 帧 · {{ (item.duration_ms / 1000).toFixed(1) }}s · {{ fmtTime(item.updated_at) }}</div>
            <div class="rec-ops">
              <button title="载入并播放" @click="playItem(item)">▶ 播放</button>
              <button title="仅载入" @click="loadItem(item)">⤓ 载入</button>
              <button title="重命名" @click="renameItem(item)">✎</button>
              <button title="导出 JSON" @click="rec.download(item)">⬇</button>
              <button title="删除" @click="deleteItem(item)">🗑</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </MonitorLayout>
</template>
