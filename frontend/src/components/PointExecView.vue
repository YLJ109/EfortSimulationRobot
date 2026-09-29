<script setup>
// =====================================================================
// 点位执行（从原「点位执行」页拆分出来的"点位"部分）
//   滑块示教（关节角 / 直角坐标 + 残影预演）
//   J1~J6 点动（增量 + 连续，带看门狗）
//   预设点位 CRUD / 导入导出 / 单点执行
//   + 摄像头窗口：★ 直接复用「真实监控」的同一个 CameraPanel，不另写一份
//
// 拆分的理由：示教/点动是"把机器人挪到位"的慢操作，程序执行是"跑一串动作"
// 的连续操作，两者混在一页时右侧栏要滚很长，且运行程序期间还允许拖滑块示教，
// 互相抢控制权。分开后每页只做一件事。
//
// 安全规则（前端拦截，后端再校验）全部在 stores/exec.js 里统一实现。
// =====================================================================
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from "vue";
import MonitorLayout from "./MonitorLayout.vue";
import Icon from "./Icon.vue";
import CameraPanel from "./CameraPanel.vue";
import ExecControlCard from "./ExecControlCard.vue";
import RcReadyCard from "./RcReadyCard.vue";
import { useAuthStore } from "../stores/auth.js";
import { useRobotStore } from "../stores/robot.js";
import { useSafetyStore } from "../stores/safety.js";
import { useExecStore, JOG_STEPS } from "../stores/exec.js";
import { useRcReadyStore } from "../stores/rcReady.js";
import { apiUrl } from "../config.js";
import { apiControl } from "../net/control.js";
import { highlightJoint } from "../three/manager.js";

const auth = useAuthStore();
const robot = useRobotStore();
const safe = useSafetyStore();
const exec = useExecStore();
const rc = useRcReadyStore();

// ★ 控制权限必须同时满足：有令牌 + 机器人已连接
const canControl = computed(() => auth.controlActive && robot.connected && !robot.telemetryStale);

// ★ 需求：滑块示教 / 点动 J1~J6 的**所有组件**（含角速度、步长等设置项）只有在
//   「一键就绪」成功后（伺服上电 + 程序运行）才允许修改/点击 —— 否则整块锁死。
//   伺服没上电时改这些也没用，还会让人以为"点了没反应 = 坏了"。
const readyOk = computed(() => rc.ready);

const fileInput = ref(null);

// ---- 编辑表单（页面局部 UI 状态，不必进 store）----
const editing = ref(null);            // null | 'new' | pointId
const form = reactive({
  id: null, name: "", group: "默认", kind: "joint",
  joints: [0, 0, 0, 0, 0, 0],
  tcp: { x: 300, y: 0, z: 700, rx: 0, ry: 0, rz: 0 },
  note: "",
});
const formErr = ref("");

const LIMITS = computed(() => exec.limits);

function openNew() {
  editing.value = "new";
  form.id = null; form.name = ""; form.group = "默认"; form.kind = "joint";
  form.joints = [0, 0, 0, 0, 0, 0];
  form.tcp = { x: 300, y: 0, z: 700, rx: 0, ry: 0, rz: 0 };
  form.note = ""; formErr.value = "";
}
function openEdit(p) {
  editing.value = p.id;
  form.id = p.id; form.name = p.name; form.group = p.group; form.kind = p.kind;
  form.joints = (p.joints || [0, 0, 0, 0, 0, 0]).slice();
  form.tcp = p.tcp && typeof p.tcp === "object"
    ? { ...form.tcp, ...p.tcp } : { x: 300, y: 0, z: 700, rx: 0, ry: 0, rz: 0 };
  form.note = p.note || ""; formErr.value = "";
}
function cancelEdit() { editing.value = null; }

async function savePoint() {
  formErr.value = "";
  if (!form.name.trim()) { formErr.value = "请填写点位名称"; return; }
  let joints = form.joints.slice();
  if (form.kind === "cartesian") {
    const r = await exec.solveTcp(form.tcp);
    if (!r.ok) { formErr.value = r.error; return; }
    joints = r.joints;
  }
  const res = await exec.savePoint({
    name: form.name, group: form.group, kind: form.kind,
    joints, note: form.note,
    tcp: form.kind === "cartesian" ? form.tcp : null,
  }, editing.value === "new" ? null : form.id);
  if (res.ok) editing.value = null;
  else formErr.value = res.error;
}

async function delPoint(p) {
  if (!confirm(`确认删除点位「${p.name}」？`)) return;
  await exec.delPoint(p);
}

/** 把当前示教姿态填进"新建点位"表单。 */
function saveTeachAsPoint() {
  const seed = exec.teachAsPointPayload();
  openNew();
  form.kind = seed.kind;
  form.joints = seed.joints;
  form.name = seed.name;
}

function exportPoint(p) { window.open(apiUrl(`/points/${p.id}/export`), "_blank"); }

/** 点列表里的点 → 选中（残影常驻跟随该点位姿）。与悬停临时预演区分开。 */
function onSelectPoint(p) { exec.pinPoint(p); }
/** 鼠标移出某点：若已选中（pin）其它点则回到该选中点的残影；否则收起。 */
function onLeavePoint(p) {
  if (exec.pinnedPointId != null) {
    const pp = exec.points.find((x) => x.id === exec.pinnedPointId);
    if (pp) exec.pinPoint(pp); else exec.hideGhost();
  } else {
    exec.unhoverPoint();
  }
}
/** 是否「标记当前点」生成的标记点（用于显示「已标记」徽标）。 */
function isMarked(p) { return !!(p && p.name && p.name.startsWith("标记点")); }

function triggerImport() { fileInput.value && fileInput.value.click(); }
async function onImport(e) {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    const r = await apiControl("/points/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: data.name || "导入点位", group: data.group || "导入",
        kind: data.kind || "joint", joints: data.joints || [0, 0, 0, 0, 0, 0],
        tcp: data.tcp || null, note: data.note || "",
      }),
    });
    if (r.ok) await exec.loadPoints();
    else if (r.status === 401 || r.status === 403) { if (auth.requestLogin) auth.requestLogin(); }
    else alert("导入失败");
  } catch (err) { alert("导入文件解析失败：" + err.message); }
  e.target.value = "";
}

// ---- 页面生命周期：进入时同步示教基准，离开时收尾（残影/点动/姿态接管）----
watch(() => robot.activeView, (v) => {
  if (v === "point") exec.syncTeachFromRobot();
  else exec.leaveView();
});
onBeforeUnmount(() => exec.dispose());

onMounted(async () => {
  await exec.loadAll();
  if (exec.pointsErr) exec.logLine?.("warn", exec.pointsErr);
  if (auth.controlActive) exec.refreshState();
  setTimeout(() => exec.syncTeachFromRobot(), 300);
});

// 挂载时若还没有控制权限，loadFiles 会被 apiControl 在本地拦下（不发请求、不打 401）；
// 拿到权限后再补一次，用户不必为了看文件清单去刷新页面。
watch(() => auth.controlActive, (v) => {
  if (v) { exec.loadFiles(); exec.refreshState(); }
});
</script>

<template>
  <MonitorLayout view="point">
    <template #hud>
      <div class="exec-hud">
        点位执行 · {{ safe.lastSource === "ghost" ? "残影预演" : "实时位姿" }} ·
        {{ safe.lastState === "safe" ? "安全" : "告警：" + safe.lastState }}
      </div>
      <!-- 摄像头：与「真实监控」用的是同一个 CameraPanel（同一份状态与开关） -->
      <CameraPanel :views="['point', 'live']" />
    </template>

    <ExecControlCard />

    <!-- 真机链路就绪：控制器状态 + 一键就绪 + 档位声明。
         ★ 点动被拒时这里逐行给出原因（档位/伺服/报警/程序/触发位）。 -->
    <RcReadyCard />

    <!-- 滑块示教 -->
    <div class="card">
      <h3><Icon name="sliders" :size="15" /> 滑块示教
        <span class="h3-sub">拖动即预演（残影跟随），确认后下发</span>
      </h3>
      <!-- ★ 需求：示教区的**所有组件**在"未就绪"时一律不可修改/不可点（伺服没上电时改了也没用）。 -->
      <p v-if="!readyOk" class="lock-hint">
        <Icon name="lock" :size="13" /> 需先在「真机链路」点「一键就绪」（伺服上电 + 程序运行）后，才可示教。
      </p>
      <div class="seg">
        <button :class="{ on: exec.teachMode === 'joint' }" :disabled="!readyOk"
                @click="exec.teachMode = 'joint'; exec.schedulePreview()">关节角</button>
        <button :class="{ on: exec.teachMode === 'cartesian' }" :disabled="!readyOk"
                @click="exec.teachMode = 'cartesian'; exec.schedulePreview()">直角坐标</button>
        <label class="ghost-toggle">
          <input type="checkbox" v-model="exec.teachGhost" :disabled="!readyOk"
                 @change="exec.teachGhost ? exec.schedulePreview() : exec.hideGhost()" />
          残影预演
        </label>
      </div>

      <template v-if="exec.teachMode === 'joint'">
        <div class="axis" v-for="(n, i) in robot.axes" :key="n"
             @mouseenter="highlightJoint(i)" @mouseleave="highlightJoint(-1)">
          <label>{{ n }}</label>
          <input type="range" :min="LIMITS[i].min" :max="LIMITS[i].max" step="0.5"
                 :disabled="!readyOk"
                 :value="exec.teachQ[i]" @input="exec.onTeachSlider(i, $event)" />
          <span class="deg">{{ Number(exec.teachQ[i]).toFixed(1) }}</span>
        </div>
      </template>
      <template v-else>
        <div class="axis" v-for="ax in ['x', 'y', 'z']" :key="ax">
          <label>{{ ax.toUpperCase() }}</label>
          <input type="number" class="tcp-in" v-model.number="exec.teachTcp[ax]"
                 :disabled="!readyOk"
                 @change="exec.schedulePreview()" />
          <span class="deg">mm</span>
        </div>
        <div class="btns" style="margin-top:6px">
          <button :disabled="!readyOk || exec.teachBusy" @click="exec.solveCartesian()">
            <Icon name="target" :size="13" /> 解算为关节角
          </button>
        </div>
      </template>

      <div class="pf-row" style="margin-top:8px">
        <label>速度</label>
        <input type="range" min="1" max="100" v-model.number="exec.teachSpeed" :disabled="!readyOk" />
        <span class="v" style="width:44px;text-align:right">{{ exec.teachSpeed }}%</span>
      </div>

      <div v-if="exec.teachResult" class="teach-res"
           :class="exec.teachResult.ok ? 'ok' : 'err'">
        <Icon :name="exec.teachResult.ok ? 'check' : 'alert'" :size="13" />
        <span v-if="exec.teachResult.ok">
          目标可行 · 行程 {{ (exec.teachResult.distance_mm || 0).toFixed(1) }} mm
          <template v-if="exec.teachResult.warnings && exec.teachResult.warnings.length">
            · {{ exec.teachResult.warnings[0] }}
          </template>
        </span>
        <span v-else>
          {{ exec.teachResult.error
            || (exec.teachResult.violations && exec.teachResult.violations.length
                ? '超出关节限位' : '目标不可行') }}
        </span>
      </div>

      <div class="btns">
        <button class="primary" :disabled="!exec.canExec || exec.teachBusy"
                @click="exec.applyTeach()">
          <Icon name="target" :size="13" /> 执行到位姿
        </button>
        <button :disabled="!readyOk || exec.teachBusy" @click="exec.syncTeachFromRobot()">
          <Icon name="refresh" :size="13" /> 取当前位姿
        </button>
        <button :disabled="!readyOk" @click="saveTeachAsPoint">
          <Icon name="file" :size="13" /> 存为点位
        </button>
      </div>
    </div>

    <!-- 点动 -->
    <div class="card">
      <h3><Icon name="grip" :size="15" /> 点动 J1~J6
        <span class="h3-sub">
          {{ exec.jogState && exec.jogState.active
            ? ('运行中 J' + exec.jogState.joint + ' · 看门狗 ' + exec.jogState.watchdog_left_ms + 'ms')
            : '按住连续走 / 单击走一格' }}
        </span>
      </h3>
      <!-- ★ 需求：点动区所有组件同样要求"已就绪"（不只按钮，角速度/步长也不可改）。 -->
      <p v-if="!readyOk" class="lock-hint">
        <Icon name="lock" :size="13" /> 需先在「真机链路」点「一键就绪」（伺服上电 + 程序运行）后才可点动。
      </p>
      <div class="pf-row">
        <label>角速度</label>
        <input type="range" min="1" max="10" v-model.number="exec.jogSpeed" :disabled="!readyOk" />
        <span class="v" style="width:45px;text-align:right">{{ exec.jogSpeed }}°/s</span>
      </div>
      <div class="pf-row">
        <label>步长</label>
        <div class="seg mini">
          <button v-for="s in JOG_STEPS" :key="s" :class="{ on: exec.jogStepDeg === s }"
                  :disabled="!readyOk" @click="exec.jogStepDeg = s">{{ s }}°</button>
        </div>
      </div>
      <div class="jog-grid">
        <div class="jog-row" v-for="(n, i) in robot.axes" :key="'jog' + n"
             @mouseenter="highlightJoint(i)" @mouseleave="highlightJoint(-1)">
          <span class="jog-name">{{ n }}</span>
          <button class="jog-btn neg" :disabled="!exec.canJog || exec.jogBusy"
                  @pointerdown.prevent="exec.jogPress(i + 1, -1)"
                  @pointerup="exec.jogRelease" @pointerleave="exec.jogRelease"
                  @pointercancel="exec.jogRelease"
                  :title="'按住连续反向 / 单击走 ' + exec.jogStepDeg + '°'">
            <Icon name="chevronDown" :size="13" /> 反向
          </button>
          <button class="jog-btn pos" :disabled="!exec.canJog || exec.jogBusy"
                  @pointerdown.prevent="exec.jogPress(i + 1, 1)"
                  @pointerup="exec.jogRelease" @pointerleave="exec.jogRelease"
                  @pointercancel="exec.jogRelease"
                  :title="'按住连续正向 / 单击走 ' + exec.jogStepDeg + '°'">
            <Icon name="chevronUp" :size="13" /> 正向
          </button>
        </div>
      </div>
      <p class="small">
        连续点动期间前端每 0.4s 发一次"使能保持"；<b>松开鼠标、切走窗口或 1.5s 无信号即自动停</b>
        （对齐示教器三段使能开关）。急停 / 围栏危险 / 到限位同样立即停。
      </p>
    </div>

    <!-- 预设点位 -->
    <div class="card">
      <h3><Icon name="database" :size="15" /> 预设点位
        <span class="h3-sub">{{ exec.points.length }} 个</span>
      </h3>
      <div class="btns" style="margin-bottom:10px">
        <button class="primary" @click="openNew"><Icon name="edit" :size="14" /> 新建点位</button>
        <button :disabled="!canControl"
                :title="canControl ? '读取机器人当前 6 个关节坐标存为点位（T1/T2 示教器手动对位也可，仅记录不运动）'
                                  : '需控制权限且机器人已连接、遥测可信'"
                @click="exec.markCurrentPoint">
          <Icon name="pin" :size="14" /> 标记当前点
        </button>
        <button @click="triggerImport"><Icon name="upload" :size="14" /> 导入</button>
        <button v-if="exec.runBusy" class="warn" @click="exec.abortRun()">
          <Icon name="stop" :size="14" /> 停止
        </button>
        <input ref="fileInput" type="file" accept="application/json" style="display:none"
               @change="onImport" />
      </div>
      <p class="small pt-hint">
        <Icon name="pin" :size="12" /> 「标记当前点」读取机器人实时位姿（T1/T2 示教器手动对位也可用，仅记录、不发送运动）；
        点列表里任意点位 → 残影常驻跟随其位置；「执行」需在 AUTO 下一键就绪后。
      </p>

      <div v-if="editing" class="pt-form">
        <div class="pf-row">
          <label>名称</label>
          <input v-model="form.name" type="text" placeholder="点位名称" />
          <label>分组</label>
          <input v-model="form.group" type="text" />
        </div>
        <div class="pf-row">
          <label>类型</label>
          <select v-model="form.kind">
            <option value="joint">关节角</option>
            <option value="cartesian">直角坐标</option>
          </select>
        </div>
        <template v-if="form.kind === 'joint'">
          <!-- ★ pf-grid6：六等分列（标签一行 + 输入一行），六个框等宽；
               不能用 pf-grid 的 28px/1fr 成对列模板（会宽窄不一，见现场截图） -->
          <div class="pf-grid6">
            <label v-for="i in 6" :key="i">J{{ i }}</label>
            <input v-for="i in 6" :key="'j' + i" type="number" step="1"
                   v-model.number="form.joints[i - 1]" />
          </div>
        </template>
        <template v-else>
          <div class="pf-grid">
            <label>X</label><input type="number" v-model.number="form.tcp.x" />
            <label>Y</label><input type="number" v-model.number="form.tcp.y" />
            <label>Z</label><input type="number" v-model.number="form.tcp.z" />
            <label>Rx</label><input type="number" v-model.number="form.tcp.rx" />
            <label>Ry</label><input type="number" v-model.number="form.tcp.ry" />
            <label>Rz</label><input type="number" v-model.number="form.tcp.rz" />
          </div>
          <p class="small">直角坐标将在保存时经逆运动学解析为关节角（需控制权限）。</p>
        </template>
        <div class="pf-row">
          <label>备注</label>
          <input v-model="form.note" type="text" placeholder="可选" />
        </div>
        <p v-if="formErr" class="pf-err"><Icon name="alert" :size="13" /> {{ formErr }}</p>
        <div class="btns">
          <button class="primary" @click="savePoint"><Icon name="check" :size="14" /> 保存</button>
          <button @click="cancelEdit">取消</button>
        </div>
      </div>

      <div class="pt-list">
        <div v-for="p in exec.points" :key="p.id" class="pt-item"
             :class="{ selected: exec.pinnedPointId === p.id }"
             @mouseenter="exec.hoverPoint(p)" @mouseleave="onLeavePoint(p)"
             @click="onSelectPoint(p)">
          <div class="pt-main">
            <span class="pt-name">{{ p.name }}</span>
            <div class="pt-tags">
              <span v-if="isMarked(p)" class="pt-tag marked"><Icon name="pin" :size="11" /> 已标记</span>
              <span class="pt-tag">{{ p.group }}</span>
              <span class="pt-tag" :class="{ warn: p.kind === 'cartesian' }">
                {{ p.kind === "cartesian" ? "直角" : "关节" }}
              </span>
            </div>
          </div>
          <div class="pt-coords">
            <span class="coord" v-for="(jv, ji) in p.joints" :key="ji">
              <b>J{{ ji + 1 }}</b>{{ Number(jv).toFixed(3) }}
            </span>
          </div>
          <div v-if="p.note" class="pt-note"><Icon name="note" :size="12" /> {{ p.note }}</div>
          <div class="pt-ops" @click.stop>
            <button :disabled="!exec.canExec || exec.runBusy" @click="exec.execPoint(p)">
              <Icon name="target" :size="13" /> 执行
            </button>
            <button :disabled="!canControl" title="把残影摆到该点位预演"
                    @click="exec.previewPointGhost(p.id)">
              <Icon name="sliders" :size="13" /> 预演
            </button>
            <button @click="openEdit(p)"><Icon name="edit" :size="13" /> 编辑</button>
            <button @click="exportPoint(p)"><Icon name="download" :size="13" /> 导出</button>
            <button class="pt-del" @click="delPoint(p)"><Icon name="trash" :size="13" /> 删除</button>
          </div>
        </div>
        <p v-if="!exec.points.length" class="small">
          暂无点位，点击「新建点位」创建，或用滑块示教后「存为点位」。
        </p>
      </div>
    </div>
  </MonitorLayout>
</template>

<style scoped>
/* ★ 未就绪时示教/点动区的"锁"提示条（说明为什么整块点不动） */
.lock-hint {
  display: flex; align-items: center; gap: 6px;
  margin: 2px 0 8px; padding: 7px 10px;
  font-size: 11px; line-height: 1.5;
  color: var(--warn); background: var(--warn-soft);
  border: 1px solid var(--warn-line); border-radius: 8px;
}
/* ★ 点位卡片：T1/T2 提示条 + 选中高亮 + 备注行 */
.pt-hint {
  display: flex; align-items: flex-start; gap: 5px;
  margin: -2px 0 8px; line-height: 1.5;
}
.pt-hint svg { color: var(--accent); flex: none; margin-top: 2px; }
/* ★ 点位列表：纵向堆叠（上下关系），取消默认横向内联 */
.pt-item {
  display: block;                 /* 取消 flex：名称 / 标签 / 坐标 / 备注 / 操作 上下排列 */
  padding: 9px 11px;
  margin-bottom: 8px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel);
  cursor: pointer;
  transition: border-color .15s, background .15s;
}
.pt-item:hover { border-color: var(--accent-line); }
.pt-item.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 3px 0 0 var(--accent);
}
.pt-main {
  display: flex; flex-direction: column; align-items: flex-start; gap: 4px;
}
.pt-name { font-size: 13px; font-weight: 600; color: var(--txt); }
.pt-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.pt-tag {
  font-size: 11px; padding: 1px 7px; border-radius: 20px;
  color: var(--muted); border: 1px solid var(--line); background: var(--panel2);
}
.pt-tag.warn { color: var(--warn); border-color: var(--warn-line); background: var(--warn-soft); }
.pt-tag.marked {
  color: var(--accent); border-color: var(--accent-line); background: var(--accent-soft);
  display: inline-flex; align-items: center; gap: 3px;
}
.pt-tag.marked svg { flex: none; }
.pt-coords { display: flex; flex-wrap: wrap; gap: 4px 8px; margin: 6px 0 2px; }
.coord {
  font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums;
  background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 1px 6px;
}
.coord b { color: var(--accent); font-weight: 600; margin-right: 3px; }
.pt-note {
  display: flex; align-items: center; gap: 5px;
  margin: 5px 2px 2px; font-size: 11px; color: var(--muted); line-height: 1.4;
}
.pt-note svg { color: var(--muted); flex: none; }
.pt-ops { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
.btns button.warn {
  color: var(--warn); border-color: var(--warn-line); background: var(--warn-soft);
}
.btns button.warn:hover:not(:disabled) { filter: brightness(0.97); }
</style>
