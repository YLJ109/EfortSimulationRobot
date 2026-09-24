<script setup>
// 机器人操作·捕捉（程序时序版）：
//   默认四个操作原语 —— 点位记录 / 吸气 / 放气 / 等待。用户手动点按录制步骤，
//   点位记录=记录当时 6 关节角；吸气=吸附碰触物体；放气=放下；等待=停留。
//   写完后点「运行」让模拟机器人按步骤顺序走关节、拾放；「停止」随时中断。
import { ref, onBeforeUnmount } from "vue";
import { useRobotStore } from "../stores/robot.js";
import { getSimObjects, getTcp } from "../three/manager.js";

const robot = useRobotStore();

const steps = ref([]);
const seq = ref(0);
const waitDur = ref(1000);        // 「等待」默认停留时长(ms)
const running = ref(false);
const activeIdx = ref(-1);
const runToken = ref(0);

const INDEX = { pose: "点位", suck: "吸气", release: "放气", wait: "等待" };
const CLS = { pose: "st-pose", suck: "st-suck", release: "st-release", wait: "st-wait" };

function fmtJoints(q) {
  return q.map((v) => v.toFixed(1)).join(" / ");
}
function pushStep(type, extra) {
  const st = { id: ++seq.value, type, done: false };
  Object.assign(st, extra || {});
  steps.value.push(st);
}
const capPose = () => pushStep("pose", { joints: robot.simQ.slice() });
const capSuck = () => pushStep("suck");
const capRelease = () => pushStep("release");
const capWait = () => pushStep("wait", { dur: Math.max(0, waitDur.value) });

function removeStep(i) { steps.value.splice(i, 1); }
function moveStep(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= steps.value.length) return;
  const a = steps.value[i]; steps.value[i] = steps.value[j]; steps.value[j] = a;
}
function clearSteps() { steps.value = []; seq.value = 0; }

function simObj() {
  const s = getSimObjects();
  if (s) return Promise.resolve(s);
  return new Promise((res) => {
    let n = 0;
    const id = setInterval(() => {
      const s2 = getSimObjects();
      if (s2 || ++n > 20) { clearInterval(id); res(s2 || null); }
    }, 60);
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 等待 3D 平滑收敛（moveJ 到点后 TCP 位置不再明显变化）
function waitSettled(timeout) {
  return new Promise((resolve) => {
    let prev = NaN;
    let t0 = performance.now();
    const id = setInterval(() => {
      const p = getTcp() || { x: 0, y: 0, z: 0 };
      const cur = p.x + p.y + p.z;
      const d = Number.isNaN(prev) ? 1 : Math.abs(cur - prev);
      prev = cur;
      if (d < 0.002 || performance.now() - t0 > timeout) { clearInterval(id); resolve(); }
    }, 60);
  });
}

async function run() {
  if (running.value || !steps.value.length) return;
  running.value = true;
  const token = ++runToken.value;
  const sim = await simObj();
  try {
    for (let i = 0; i < steps.value.length; i++) {
      if (token !== runToken.value) break;
      activeIdx.value = i;
      const st = steps.value[i];
      if (st.type === "pose") {
        robot.setSimQ(st.joints);
        await waitSettled(2000);
      } else if (st.type === "suck") {
        sim?.setSuck(true);
        await sleep(600);
      } else if (st.type === "release") {
        sim?.setSuck(false);
        await sleep(600);
      } else if (st.type === "wait") {
        await sleep(Math.max(0, +st.dur || 1000));
      }
      if (token !== runToken.value) break;
    }
  } finally {
    if (token === runToken.value) { activeIdx.value = -1; running.value = false; }
  }
}
function stop() { runToken.value++; running.value = false; activeIdx.value = -1; }

onBeforeUnmount(() => { runToken.value++; running.value = false; });
</script>

<template>
  <div class="card">
    <h3>机器人操作 · 捕捉</h3>
    <div class="btns rp-grid">
      <button class="primary" :disabled="running" @click="capPose">点位记录</button>
      <button :disabled="running" @click="capSuck">吸气</button>
      <button :disabled="running" @click="capRelease">放气</button>
      <button :disabled="running" @click="capWait">等待</button>
      <div class="rp-wait">
        <span class="rp-wk">停留</span>
        <input type="number" v-model.number="waitDur" step="100" min="0" style="width:64px" />
        <span class="rp-wu">ms</span>
      </div>
    </div>

    <div class="rp-list">
      <div v-if="!steps.length" class="rp-empty">按上方按钮记录步骤，再点「运行」在仿真中执行。</div>
      <div v-for="(st, i) in steps" :key="st.id" class="rp-item" :class="[CLS[st.type], { 'rp-run': i === activeIdx }]">
        <span class="rp-no">{{ i + 1 }}</span>
        <span class="rp-type">{{ INDEX[st.type] }}</span>
        <span class="rp-detail">
          <template v-if="st.type === 'pose'">J1~J6 · {{ fmtJoints(st.joints) }}</template>
          <template v-else-if="st.type === 'wait'">停留 {{ st.dur }} ms</template>
          <template v-else>—</template>
        </span>
        <span class="rp-ops">
          <button :disabled="running" title="上移" @click="moveStep(i, -1)">↑</button>
          <button :disabled="running" title="下移" @click="moveStep(i, 1)">↓</button>
          <button :disabled="running" title="删除" @click="removeStep(i)">×</button>
        </span>
      </div>
    </div>

    <div class="btns rp-runbar">
      <button class="primary" :disabled="running || !steps.length" @click="run">{{ running ? "执行中…" : "运行" }}</button>
      <button :disabled="!running" @click="stop">停止</button>
      <button class="rp-clear" :disabled="running" @click="clearSteps">清空</button>
    </div>

    <div class="rp-steps">
      <div v-if="!steps.length" class="rp-empty">先用右侧「关节角」滑块摆姿 → 点「点位记录」抓取 J1~J6，再按序加吸气/放气/等待，点「运行」顺序执行。</div>
      <div v-for="(st, i) in steps" :key="st.id" :class="['rp-item', CLS[st.type], { 'rp-run': i === activeIdx }]">
        <span class="rp-no">{{ String(i + 1).padStart(2, "0") }}</span>
        <span class="rp-code">
          <span class="rp-kw">{{ INDEX[st.type] }}</span>
          <span class="rp-args" v-if="st.type === 'pose'">( {{ fmtJoints(st.joints) }} )</span>
          <span class="rp-args" v-else-if="st.type === 'wait'">( {{ st.dur }} ms )</span>
          <span class="rp-args" v-else>; // {{ st.type.toUpperCase() }}</span>
        </span>
        <span class="rp-ops">
          <button :disabled="running" title="上移" @click="moveStep(i, -1)">↑</button>
          <button :disabled="running" title="下移" @click="moveStep(i, 1)">↓</button>
          <button :disabled="running" title="删除" @click="removeStep(i)">×</button>
        </span>
      </div>
    </div>

    <div class="rp-hint">程序从上到下顺序执行：点位=走关节到该姿态，吸气=吸附接触物体，放气=放下，等待=停留。</div>
  </div>
</template>

<style scoped>
.rp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.rp-wait { display: flex; align-items: center; gap: 4px; grid-column: 1 / -1; font-size: 12px; color: var(--muted); }
.rp-wk { white-space: nowrap; }
.rp-wu { color: var(--muted); }
.rp-steps { margin: 10px 0 6px; display: flex; flex-direction: column; gap: 4px; max-height: 300px; overflow-y: auto; }
.rp-empty { color: var(--muted); font-size: 12px; padding: 4px 2px; line-height: 1.5; }
.rp-item { display: flex; align-items: center; gap: 6px; font-size: 12px;
  padding: 4px 6px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel2);
  font-family: ui-monospace, Consolas, monospace; }
.rp-item.rp-run { border-color: var(--accent); background: var(--accent-soft); }
.rp-item.st-pose { border-left: 3px solid var(--c-pose); }
.rp-item.st-suck { border-left: 3px solid var(--c-suck); }
.rp-item.st-release { border-left: 3px solid var(--c-release); }
.rp-item.st-wait { border-left: 3px solid var(--c-wait); }
.rp-no { color: var(--muted); min-width: 22px; text-align: right; user-select: none; }
.rp-code { flex: 1; display: flex; gap: 5px; overflow: hidden; min-width: 0; }
.rp-kw { font-weight: 700; white-space: nowrap; }
.rp-item.st-pose .rp-kw { color: var(--c-pose); }
.rp-item.st-suck .rp-kw { color: var(--c-suck); }
.rp-item.st-release .rp-kw { color: var(--c-release); }
.rp-item.st-wait .rp-kw { color: var(--c-wait); }
.rp-args { color: var(--txt); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rp-hint { font-size: 11px; color: var(--muted); line-height: 1.5; }
.rp-ops { display: flex; gap: 2px; }
.rp-ops button { padding: 0 4px; font-size: 12px; line-height: 16px; }
.rp-runbar { display: flex; gap: 6px; }
.rp-clear { margin-left: auto; }
</style>