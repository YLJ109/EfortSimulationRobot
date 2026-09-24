<script setup>
// 模拟摆放物体 · 左侧控制面板。拆成两卡：
//   ① 物体摆放 —— 新增(按色)/删除/调大小/盒宽/坐标放置/拖放投放，只管摆物件
//   ② 机器人操作·捕捉 —— 吸气/放气/摄像头识别 三个原语 + 切视角/重置
// 纯前端模拟：不接真机、不驱动真实机器人、不接相机。分拣步骤由用户自行编排。
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { getSimObjects, flyToInspect } from "../three/manager.js";

const sim = getSimObjects();
const rev = ref(0);
const openA = ref(true);   // 物体摆放卡展开
const openB = ref(true);   // 机器人操作·捕捉卡展开
const selId = ref(null);
const size = ref(0.12);
const addColor = ref("red");
const boxWidth = ref(0.52);
const posX = ref(0);
const posZ = ref(0);
const suck = ref(false);

const COLOR_OPTS = [
  { v: "red", t: "红色", c: "var(--c-red)" },
  { v: "green", t: "绿色", c: "var(--c-green)" },
  { v: "blue", t: "蓝色", c: "var(--c-blue)" },
];
const nameOfColor = (v) => (COLOR_OPTS.find((c) => c.v === v) || {}).t || v;

function load() {
  const snap = sim.getSnapshot();
  const cur = snap.find((o) => o.id === selId.value);
  if (cur) { size.value = cur.size; posX.value = +cur.x.toFixed(2); posZ.value = +cur.z.toFixed(2); }
  return snap;
}
const list = computed(() => { rev.value; return load(); });
const shown = computed(() => list.value);

function pick(id) { selId.value = id; sim.select(id); load(); }
function doAdd() { pick(sim.add(addColor.value)); }
function doDelete() {
  if (selId.value == null) return;
  sim.remove(selId.value);
  pick(null);
}
function applyPos() { if (selId.value != null) sim.moveTo(selId.value, posX.value, posZ.value); }
function onSize(e) {
  size.value = parseFloat(e.target.value) || 0.12;
  if (selId.value != null) sim.resize(selId.value, size.value);
}
function onBoxWidth(e) {
  boxWidth.value = parseFloat(e.target.value) || 0.52;
  sim.setBoxWidth(boxWidth.value);
}

// 卡片拖放：拖动行 = 移动该物体；拖动颜色块 = 到 3D 落点新增
function dragStart(e, payload) {
  e.dataTransfer.setData("application/x-simobj", payload);
  e.dataTransfer.effectAllowed = "move";
}
function doIdentify() { sim.identify(selId.value); }
function doIdentifyAll() { sim.identify(null); }
function doSuck() { suck.value = !suck.value; sim.setSuck(suck.value); }
function doRelease() { if (suck.value) { suck.value = false; sim.setSuck(false); } }
function resetScene() {
  sim.reset();
  selId.value = null;
  suck.value = false;
  load();
}

onMounted(() => { sim.onChange = () => rev.value++; load(); });
onBeforeUnmount(() => { sim.onChange = null; });
const dotColor = (o) => o.revealed ? nameOfColor(o.colorText) === "红色" ? "var(--c-red)"
  : nameOfColor(o.colorText) === "绿色" ? "var(--c-green)" : "var(--c-blue)" : "var(--c-none)";
</script>

<template>
  <div class="sim-panel">

    <!-- 卡片A：物体摆放 -->
    <div class="sim-card" :class="{ pad0: !openA }">
      <div class="sim-head" title="折叠/展开" @click="openA = !openA">
        <span class="sim-title">物体摆放</span>
        <span class="sim-caret">{{ openA ? '▾' : '▸' }}</span>
      </div>
      <div v-show="openA">
        <div class="small" style="margin-bottom:6px">
          拖<b>颜色块</b>到 3D = 新增；拖<b>列表行</b> = 移动该物体；点 3D 物体也可选中拖拽。
        </div>

        <div class="row">
          <span class="k">盒宽</span>
          <input type="range" min="0.4" max="1.0" step="0.02" :value="boxWidth" @input="onBoxWidth" />
          <span class="deg">{{ boxWidth.toFixed(2) }}m</span>
        </div>

        <div class="btns" style="margin:8px 0">
          <button class="small-txt">新增：</button>
          <button v-for="opt in COLOR_OPTS" :key="opt.v" class="cbtn" :class="'c-' + opt.v"
                  :title="'拖到 3D 以' + opt.t + '放下，或点击在源盒新增'"
                  :draggable="true" @click="addColor = opt.v; doAdd()"
                  @dragstart="dragStart($event, 'new:' + opt.v)">{{ opt.t }}</button>
          <button class="danger" :disabled="selId == null" @click="doDelete">删除</button>
        </div>

        <div class="row"><span class="k">选中</span>
          <span class="v">{{ selId == null ? '—' : '#' + selId }}</span></div>
        <div class="row"><span class="k">大小</span>
          <input type="range" min="0.04" max="0.3" step="0.01" :value="size" @input="onSize" />
          <span class="deg">{{ size.toFixed(2) }}m</span>
        </div>
        <div class="row">
          <span class="k">X 定位</span>
          <input type="number" v-model.number="posX" step="0.05" min="-2" max="2" />
        </div>
        <div class="row">
          <span class="k">Z 定位</span>
          <input type="number" v-model.number="posZ" step="0.05" min="-2" max="2" />
        </div>
        <div class="btns" style="margin:6px 0">
          <button class="primary" :disabled="selId == null" @click="applyPos">按坐标放置</button>
        </div>

        <div class="sublist" v-if="shown.length">
          <div v-for="o in shown" :key="o.id"
               class="subitem" :class="{ sel: o.id === selId }"
               :draggable="true" :title="'拖到 3D 移动 #' + o.id"
               @click="pick(o.id)" @dragstart="dragStart($event, 'move:' + o.id)">
            <span class="dot" :style="{ background: dotColor(o) }"></span>
            <span class="sub-name">#{{ o.id }}</span>
            <span class="sub-tag">{{ o.revealed ? nameOfColor(o.colorText) : '黑色' }}</span>
            <span class="sub-tag" v-if="o.held">已吸</span>
            <span class="sub-tag sub-mono">{{ o.x.toFixed(2) }},{{ o.z.toFixed(2) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 卡片B：机器人操作·捕捉 -->
    <div class="sim-card" :class="{ pad0: !openB }">
      <div class="sim-head" title="折叠/展开" @click="openB = !openB">
        <span class="sim-title">机器人操作·捕捉</span>
        <span class="sim-caret">{{ openB ? '▾' : '▸' }}</span>
      </div>
      <div v-show="openB">
        <div class="btns" style="margin:8px 0">
          <button class="primary" @click="doSuck">{{ suck ? '放气' : '吸气' }}</button>
          <button :disabled="!suck" @click="doRelease">放气</button>
          <button :disabled="selId == null" @click="doIdentify">识别选中</button>
          <button @click="doIdentifyAll">识别全部</button>
        </div>
        <div class="small" style="color:var(--muted);margin-bottom:8px">
          吸气=末端靠近某物体即吸附带走；放气=就地放下；摄像头识别=黑色物体显本色并拍一张留档。
        </div>
        <div class="btns" style="margin:8px 0">
          <button @click="flyToInspect">切摄像头视角</button>
          <button @click="resetScene">重置</button>
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
.sim-panel { display: flex; flex-direction: column; gap: 8px; }
.sim-card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
.sim-card.pad0 { padding: 0; }
.sim-head { display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;
  padding: 8px 10px; }
.sim-card:not(.pad0) .sim-head { border-bottom: 1px solid var(--line); }
.sim-card > div:not(.sim-head) { padding: 0 10px 10px; }
.sim-title { font-size: 14px; font-weight: 700; }
.sim-caret { margin-left: auto; color: var(--muted); font-size: 13px; }
.btns { display: flex; flex-wrap: wrap; gap: 6px; }
.btns button { margin: 0; }
.small-txt { background: transparent !important; color: var(--muted); border: none; cursor: default; }
.cbtn { position: relative; }
.cbtn::before { content: ""; display: inline-block; width: 9px; height: 9px; border-radius: 50%;
  margin-right: 4px; vertical-align: middle; }
.cbtn.c-red::before { background: var(--c-red); }
.cbtn.c-green::before { background: var(--c-green); }
.cbtn.c-blue::before { background: var(--c-blue); }
.sublist { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.subitem { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-left: 3px solid var(--line2);
  background: var(--panel2); border-radius: 4px; cursor: grab; font-size: 13px; }
.subitem.sel { outline: 1px solid var(--accent); }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.sub-name { font-weight: 600; }
.sub-tag { margin-left: auto; font-size: 11px; color: var(--muted); }
.sub-mono { font-family: Consolas, monospace; }
button.danger { color: var(--err); }
</style>