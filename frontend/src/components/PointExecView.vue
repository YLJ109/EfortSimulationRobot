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
import { apiUrl, cameraBase } from "../config.js";
import { apiControl } from "../net/control.js";
import { highlightJoint } from "../three/manager.js";

const auth = useAuthStore();
const robot = useRobotStore();
const safe = useSafetyStore();
const exec = useExecStore();

// ★ 控制权限必须同时满足：有令牌 + 机器人已连接
const canControl = computed(() => auth.controlActive && robot.connected);

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
      <div class="seg">
        <button :class="{ on: exec.teachMode === 'joint' }"
                @click="exec.teachMode = 'joint'; exec.schedulePreview()">关节角</button>
        <button :class="{ on: exec.teachMode === 'cartesian' }"
                @click="exec.teachMode = 'cartesian'; exec.schedulePreview()">直角坐标</button>
        <label class="ghost-toggle">
          <input type="checkbox" v-model="exec.teachGhost"
                 @change="exec.teachGhost ? exec.schedulePreview() : exec.hideGhost()" />
          残影预演
        </label>
      </div>

      <template v-if="exec.teachMode === 'joint'">
        <div class="axis" v-for="(n, i) in robot.axes" :key="n"
             @mouseenter="highlightJoint(i)" @mouseleave="highlightJoint(-1)">
          <label>{{ n }}</label>
          <input type="range" :min="LIMITS[i].min" :max="LIMITS[i].max" step="0.5"
                 :value="exec.teachQ[i]" @input="exec.onTeachSlider(i, $event)" />
          <span class="deg">{{ Number(exec.teachQ[i]).toFixed(1) }}</span>
        </div>
      </template>
      <template v-else>
        <div class="axis" v-for="ax in ['x', 'y', 'z']" :key="ax">
          <label>{{ ax.toUpperCase() }}</label>
          <input type="number" class="tcp-in" v-model.number="exec.teachTcp[ax]"
                 @change="exec.schedulePreview()" />
          <span class="deg">mm</span>
        </div>
        <div class="btns" style="margin-top:6px">
          <button :disabled="!canControl || exec.teachBusy" @click="exec.solveCartesian()">
            <Icon name="target" :size="13" /> 解算为关节角
          </button>
        </div>
      </template>

      <div class="pf-row" style="margin-top:8px">
        <label>速度</label>
        <input type="range" min="1" max="100" v-model.number="exec.teachSpeed" />
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
        <button :disabled="!canControl || exec.teachBusy" @click="exec.syncTeachFromRobot()">
          <Icon name="refresh" :size="13" /> 取当前位姿
        </button>
        <button :disabled="!canControl" @click="saveTeachAsPoint">
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
      <div class="pf-row">
        <label>角速度</label>
        <input type="range" min="1" max="10" v-model.number="exec.jogSpeed" />
        <span class="v" style="width:45px;text-align:right">{{ exec.jogSpeed }}°/s</span>
      </div>
      <div class="pf-row">
        <label>步长</label>
        <div class="seg mini">
          <button v-for="s in JOG_STEPS" :key="s" :class="{ on: exec.jogStepDeg === s }"
                  @click="exec.jogStepDeg = s">{{ s }}°</button>
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
        <button @click="triggerImport"><Icon name="upload" :size="14" /> 导入</button>
        <input ref="fileInput" type="file" accept="application/json" style="display:none"
               @change="onImport" />
      </div>

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
          <div class="pf-grid">
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
             @mouseenter="exec.hoverPoint(p)" @mouseleave="exec.unhoverPoint()">
          <div class="pt-main">
            <span class="pt-name">{{ p.name }}</span>
            <span class="pt-tag">{{ p.group }}</span>
            <span class="pt-tag" :class="{ warn: p.kind === 'cartesian' }">
              {{ p.kind === "cartesian" ? "直角" : "关节" }}
            </span>
            <span class="pt-joints">[{{ exec.jointsSummary(p) }}]</span>
          </div>
          <div class="pt-ops">
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
