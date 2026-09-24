<script setup>
// =====================================================================
// 程序执行（从原「点位执行」页拆分出来的"程序"部分）
//   点位序列程序的运行 / 中止 / 进度 / 逐步状态
//   按文件名执行（数据库程序 / 预设点位 / programs 目录 JSON）+ 试运行 dry-run
//   运行日志（带级别与时间戳，便于现场对节拍）
//   + 摄像头窗口：★ 直接复用「真实监控」的同一个 CameraPanel，不另写一份
//
// 运行逻辑全在 stores/exec.js（execProgram / runFile / abortRun），
// 与点位执行页共用同一套权限、围栏互锁与急停。
// =====================================================================
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from "vue";
import MonitorLayout from "./MonitorLayout.vue";
import Icon from "./Icon.vue";
import CameraPanel from "./CameraPanel.vue";
import { useAuthStore } from "../stores/auth.js";
import { useRobotStore } from "../stores/robot.js";
import { useSafetyStore } from "../stores/safety.js";
import { useRcReadyStore } from "../stores/rcReady.js";
import { useExecStore } from "../stores/exec.js";
import { apiUrl } from "../config.js";

const auth = useAuthStore();
const robot = useRobotStore();
const safe = useSafetyStore();
const rc = useRcReadyStore();
const exec = useExecStore();

// 就绪提示盒：把「链路连接 + 控制器就绪 + 控制权限」合成一个结论；
// 未就绪时只给一句最该先处理的原因，不罗列整面格表。
const isReady = computed(() => robot.connected && rc.ready && auth.controlActive);
const readyText = computed(() => {
  if (!robot.connected) return "机器人未连接，请先在底部「机器人链路」重连";
  if (!rc.ready) return "控制器未就绪（伺服 / 档位 / 报警 / 急停待检查）";
  return "未获得控制权限，无法下发";
});

/** 当前展开步骤表的程序（页面局部 UI 状态）。 */
const selProgram = ref(null);

/** 文件扩展名（小写），无扩展名返回 ""。 */
function extOf(name) {
  return (name || "").includes(".")
    ? (name.split(".").pop() || "").toLowerCase() : "";
}

/**
 * 「本地程序」统一清单：数据库点位序列 + programs 目录 .json + 预设点位。
 *   （示教器 .XPL 由 exec.teachFiles 单独成卡，不在此列。）
 *   后端 /control/files 里 group="local" 的候选，再与 exec.programs 按 name 去重，
 *   避免数据库程序出现两次。
 * ★ 任一来源为空都如实显示，不拼接成假数据。
 */
const localItems = computed(() => {
  const out = [];
  const seen = new Set();
  const push = (it) => { if (!seen.has(it.name)) { seen.add(it.name); out.push(it); } };
  // 1) 数据库点位序列（公开接口 /programs，无需令牌就有）
  for (const pr of exec.programs || []) {
    push({ kind: "program", name: pr.name, id: pr.id, no: null,
           steps: pr.items_count || (pr.items || []).length || 0,
           tag: "点位序列", isXpl: false, obj: pr });
  }
  // 2) 本地文件 + 预设点位（/control/files，需控制令牌）
  for (const f of exec.localFiles || []) {
    const ext = f.file_ext || extOf(f.name);
    const label = f.kind === "point" ? "点位"
      : (ext === "json" ? "JSON" : "文件");
    push({ kind: f.kind, name: f.name, no: f.no != null ? f.no : null,
           steps: f.steps, tag: label, isXpl: false, obj: f });
  }
  return out;
});

// 「测试跑」（空跑校验）可用性：持有控制令牌且当前没有其它运行在进行。
// ★ 测试跑无论残影是否危险都能做 —— 它不下发、不移动，只是解析 + IK/限位预演。
const canTest = computed(() => auth.controlActive && !exec.fileBusy && !exec.runBusy);
/** 某个目标是否正处于测试跑中（用于列表行内"校验中…"提示）。 */
const testBusy = (name) => exec.fileBusy && exec.fileName === name;
const refetching = ref(false);
async function refetchFiles() {
  refetching.value = true;
  try { await exec.loadFiles(); } finally { refetching.value = false; }
}

// ---------- 运行日志：自动滚到底 ----------
// ★ 只在"用户本来就在底部"时才自动滚。现场经常一边运行一边往回翻日志，
//   无脑自动滚会把正在看的那几行顶走 —— 那比不滚还烦人。
const logEl = ref(null);
const stickBottom = ref(true);
function onLogScroll() {
  const el = logEl.value;
  if (!el) return;
  stickBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
}
watch(() => exec.log.length, async () => {
  if (!stickBottom.value) return;
  await nextTick();
  const el = logEl.value;
  if (el) el.scrollTop = el.scrollHeight;
});

const steps = computed(() => {
  const pr = selProgram.value;
  if (!pr) return [];
  return (pr.items || []).map((it, i) => {
    const p = exec.points.find((x) => x.id === it.point_id);
    return {
      index: i + 1,
      name: (p && p.name) || ("点位 #" + (it.point_id ?? "?")),
      pointId: it.point_id,
      speed: it.speed_pct || 100,
      dwell: it.dwell_ms || 0,
      missing: !p,
    };
  });
});

/** 运行中：已完成 / 正在执行 / 待执行 */
function stepCls(i) {
  if (!exec.trackStep || exec.runKind !== "program") return "";
  if (exec.runName !== exec.trackName) return "";
  if (i < exec.runStep) return "done";
  if (i === exec.runStep) return "on";
  return "";
}

function exportProgram(pr) { window.open(apiUrl(`/programs/${pr.id}/export`), "_blank"); }

function pickProgram(pr) {
  selProgram.value = selProgram.value && selProgram.value.id === pr.id ? null : pr;
}

watch(() => robot.activeView, (v) => { if (v !== "program") exec.leaveView(); });
onBeforeUnmount(() => { exec.dispose(); rc.stop(); });

onMounted(() => {
  exec.loadPrograms();
  exec.loadFiles();
  rc.start();
  if (auth.controlActive) exec.refreshState();
});

// 挂载时若还没有控制权限，loadFiles 会被 apiControl 在本地拦下（不发请求、不打 401）；
// 拿到权限后再补一次，用户不必为了看文件清单去刷新页面。
watch(() => auth.controlActive, (v) => {
  if (v) { exec.loadFiles(); exec.refreshState(); }
});
</script>

<template>
  <MonitorLayout view="program">
    <template #hud>
      <div class="exec-hud">
        程序执行 · {{ safe.lastSource === "ghost" ? "残影预演" : "实时位姿" }} ·
        {{ safe.lastState === "safe" ? "安全" : "告警：" + safe.lastState }}
      </div>
      <!-- 摄像头：与「真实监控」「点位执行」用的是同一个 CameraPanel -->
      <CameraPanel :views="['program', 'live']" />
    </template>

    <!-- 就绪提示盒：一屏只给一个结论 —— 就绪 / 未就绪 + 一句原因。 -->
    <div class="ready-box" :class="{ ok: isReady }">
      <span class="rb-dot"></span>
      <div class="rb-body">
        <div class="rb-title">机器人{{ isReady ? "已就绪" : "未就绪" }}</div>
        <div class="rb-sub">{{ isReady ? "可以执行代码" : readyText }}</div>
      </div>
      <Icon :name="isReady ? 'check' : 'alert'" :size="18" class="rb-ico" />
    </div>

    <!-- 运行状态 -->
    <div v-if="exec.runTotal || exec.lastRun" class="card">
      <h3><Icon name="activity" :size="15" /> 运行状态
        <span class="h3-sub">{{ exec.running ? "运行中" : "空闲" }}</span>
      </h3>
      <div class="row">
        <span class="k">目标</span>
        <span class="v">{{ exec.runName || "—" }}</span>
      </div>
      <div class="row">
        <span class="k">进度</span>
        <span class="v">
          {{ exec.runStep }}/{{ exec.runTotal || "—" }}
          <template v-if="exec.runDry">（试运行）</template>
        </span>
      </div>
      <div class="prog-bar"><i :style="{ width: exec.runPct + '%' }"></i></div>
      <div class="btns" style="margin-top:8px">
        <button class="rec-on" :disabled="!exec.running" @click="exec.abortRun()">
          <Icon name="close" :size="13" /> 中止
        </button>
      </div>
      <div v-if="exec.lastRun" class="exec-result" :class="exec.lastRun.ok ? 'ok' : 'err'">
        <Icon :name="exec.lastRun.ok ? 'check' : 'alert'" :size="13" />
        <span v-if="exec.lastRun.ok">
          上次{{ exec.lastRun.dryRun ? "试运行" : "运行" }}通过：{{ exec.lastRun.name }}
          （{{ exec.lastRun.passed }}/{{ exec.lastRun.count }} 步，
          约 {{ (exec.lastRun.duration_ms / 1000).toFixed(1) }}s）
        </span>
        <span v-else>
          上次运行中断：{{ exec.lastRun.name }}（{{ exec.lastRun.passed }}/{{ exec.lastRun.count }} 步）
          {{ exec.lastRun.error ? " · " + exec.lastRun.error : "" }}
        </span>
      </div>
    </div>

    <!-- 示教器程序（控制器可执行的 .XPL） -->
    <div class="card">
      <h3><Icon name="terminal" :size="15" /> 示教器程序
        <span class="h3-sub">{{ exec.teachFiles.length }} 个</span>
        <span class="h3-refresh" :class="{ spin: refetching }" title="重新扫描 programs 目录"
              @click="refetchFiles">
          <Icon name="refresh" :size="13" />
        </span>
      </h3>
      <p class="small muted">示教器导出的 .XPL 程序，可在控制器上执行。「测试跑」只解析校验（IK / 限位 / 可达），绝不下发、不加载、不移动机器人。</p>
      <div class="pt-list">
        <div v-for="f in exec.teachFiles" :key="'teach:' + f.name" class="pt-item"
             :class="{ on: exec.fileName === f.name }">
          <div class="pt-main">
            <span class="pt-num">{{ f.no != null ? f.no : "—" }}</span>
            <span class="pt-tag teach">示教器</span>
            <span class="pt-name">{{ f.name }}</span>
          </div>
          <div class="pt-ops">
            <button :disabled="!canTest" @click="exec.testRun(f.name)">
              <Icon name="flask" :size="13" /> {{ testBusy(f.name) ? "校验中…" : "测试跑" }}
            </button>
          </div>
        </div>
        <p v-if="!exec.teachFiles.length" class="small">
          暂无示教器程序。把示教器导出的 .XPL 放进 programs 目录后点标题右侧刷新。
        </p>
      </div>
    </div>

    <!-- 本地程序（数据库点位序列 + JSON + 预设点位） -->
    <div class="card">
      <h3><Icon name="layers" :size="15" /> 本地程序
        <span class="h3-sub">{{ localItems.length }} 个</span>
        <span class="h3-refresh" :class="{ spin: refetching }" title="重新扫描 programs 目录"
              @click="refetchFiles">
          <Icon name="refresh" :size="13" />
        </span>
      </h3>
      <p class="small muted">数据库点位序列程序、programs 目录里的 .json 程序与预设点位。「测试跑」同样只校验，不发任何指令、不移动机器人。</p>
      <div class="pt-list">
        <div v-for="it in localItems" :key="'local:' + it.kind + ':' + it.name" class="pt-item"
             :class="{ on: (it.kind === 'program' && selProgram && selProgram.id === it.id) || exec.fileName === it.name }">
          <div class="pt-main" :style="it.kind === 'program' ? 'cursor:pointer' : ''"
               @click="it.kind === 'program' && pickProgram(it.obj)">
            <span class="pt-tag" :class="it.tag === '点位序列' ? 'seq' : (it.tag === '点位' ? '' : '')">{{ it.tag }}</span>
            <span class="pt-name">{{ it.name }}</span>
            <span v-if="it.steps" class="pt-tag">{{ it.steps }} 步</span>
          </div>
          <div class="pt-ops">
            <button :disabled="!canTest" @click="exec.testRun(it.name)">
              <Icon name="flask" :size="13" /> {{ testBusy(it.name) ? "校验中…" : "测试跑" }}
            </button>
            <button v-if="it.kind === 'program'" :disabled="exec.running" @click="pickProgram(it.obj)">
              <Icon name="chevronDown" :size="13" /> 步骤
            </button>
            <button v-if="it.kind === 'program'" @click="exportProgram(it.obj)">
              <Icon name="download" :size="13" /> 导出
            </button>
          </div>
        </div>
        <p v-if="!localItems.length" class="small">
          暂无本地程序。可在点位执行页建立点位序列，或在 programs 目录放 .json；有控制令牌时自动刷新。
        </p>
      </div>

      <!-- 步骤表：运行时高亮当前步（仅点位序列程序展开） -->
      <div v-if="selProgram" class="run-steps" style="margin-top:10px">
        <div class="sp-sec-h" style="cursor:default">
          <span>步骤明细 · {{ selProgram.name }}</span>
        </div>
        <div class="pt-list">
          <div v-for="s in steps" :key="s.index" class="pt-item" :class="stepCls(s.index)">
            <div class="pt-main">
              <span class="pt-tag">#{{ s.index }}</span>
              <span class="pt-name">{{ s.name }}</span>
              <span class="pt-joints">速度 {{ s.speed }}%</span>
              <span v-if="s.dwell" class="pt-joints">停留 {{ s.dwell }}ms</span>
              <span v-if="s.missing" class="pt-tag warn">点位缺失</span>
            </div>
          </div>
          <p v-if="!steps.length" class="small">该程序没有任何步骤。</p>
        </div>
      </div>
    </div>

    <!-- 手动执行（高级：自己输入目标名） -->
    <div class="card">
      <h3><Icon name="file" :size="15" /> 手动执行
        <span class="h3-sub">示教器 / 程序 / 点位 / JSON · 高级</span>
      </h3>
      <div class="pf-row">
        <label>目标</label>
        <input list="efort-files" v-model="exec.fileName" type="text"
               placeholder="输入文件名或选择…" />
        <datalist id="efort-files">
          <option v-for="f in exec.files" :key="f.kind + ':' + f.name" :value="f.name">
            {{ f.file_ext === 'xpl' ? '示教器' : f.kind === 'program' ? '程序' : f.kind === 'point' ? '点位' : '文件'
            }}{{ f.no != null ? ' · #' + f.no : '' }}{{ f.steps ? ' · ' + f.steps + ' 步' : '' }}
          </option>
        </datalist>
        <button :disabled="exec.fileBusy" @click="exec.loadFiles()">
          <Icon name="refresh" :size="12" />
        </button>
      </div>
      <label class="ap-line"
             style="display:flex;gap:6px;align-items:center;font-size:12px;margin:6px 0">
        <input type="checkbox" v-model="exec.dryRun" />
        <Icon name="flask" :size="13" /> 试运行（空跑测试：只校验与解算，<b>绝不下发</b>）
      </label>
      <div class="btns">
        <button class="primary" :disabled="exec.fileBusy || (!exec.dryRun && !exec.canExec)"
                @click="exec.runFile()">
          <Icon :name="exec.dryRun ? 'flask' : 'play'" :size="13" />
          {{ exec.dryRun ? "试运行" : "执行" }}
        </button>
      </div>
      <div v-if="exec.fileResult" class="teach-res"
           :class="exec.fileResult.ok ? 'ok' : 'err'">
        <Icon :name="exec.fileResult.ok ? 'check' : 'alert'" :size="13" />
        <span v-if="exec.fileResult.ok">
          {{ exec.fileResult.dry_run ? "试运行通过" : "执行完成" }}：{{ exec.fileResult.name }}
          （{{ exec.fileResult.passed }}/{{ exec.fileResult.count }} 步，
          约 {{ ((exec.fileResult.duration_ms || 0) / 1000).toFixed(1) }}s）
        </span>
        <span v-else>
          {{ exec.fileResult.error || exec.fileResult.message || "失败" }}
        </span>
      </div>
      <div v-if="exec.fileResult && exec.fileResult.candidates
                 && exec.fileResult.candidates.length" class="cands">
        <span v-for="c in exec.fileResult.candidates.slice(0, 8)" :key="c.kind + c.name"
              class="cand" @click="exec.fileName = c.name">{{ c.name }}</span>
      </div>
    </div>

    <!-- 运行日志 -->
    <div class="card">
      <h3><Icon name="terminal" :size="15" /> 运行日志
        <span class="h3-sub">{{ exec.log.length }} 条</span>
      </h3>
      <div class="run-log" ref="logEl" @scroll="onLogScroll">
        <div v-for="(l, i) in exec.log" :key="i" class="rl" :class="l.level">
          <span class="rl-t">{{ l.t }}</span>
          <span>{{ l.text }}</span>
        </div>
        <p v-if="!exec.log.length" class="small">暂无日志。运行程序或执行文件后在这里留痕。</p>
      </div>
      <div class="btns" style="margin-top:8px">
        <button :disabled="!exec.log.length" @click="exec.clearLog()">
          <Icon name="trash" :size="13" /> 清空日志
        </button>
      </div>
    </div>
  </MonitorLayout>
</template>

<style scoped>
/* 就绪提示盒：一屏一个结论，视觉像"状态横幅"而非表单卡片 */
.ready-box { display: flex; align-items: center; gap: 11px; padding: 12px 14px;
  border-radius: 10px; border: 1px solid var(--warn-line);
  background: linear-gradient(160deg, var(--warn-soft), transparent 70%); }
.ready-box.ok { border-color: var(--ok-line);
  background: linear-gradient(160deg, var(--ok-soft), transparent 70%); }
.rb-dot { flex: none; width: 9px; height: 9px; border-radius: 50%;
  background: var(--warn); box-shadow: 0 0 8px var(--warn); }
.ready-box.ok .rb-dot { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
.rb-body { flex: 1; min-width: 0; }
.rb-title { font-size: 14px; font-weight: 700; color: var(--txt); }
.rb-sub { font-size: 11px; color: var(--muted); margin-top: 2px; line-height: 1.5; }
.rb-ico { flex: none; color: var(--warn); }
.ready-box.ok .rb-ico { color: var(--ok); }
</style>
