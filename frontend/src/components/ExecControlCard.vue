<script setup>
// 执行域公用卡片：控制权限 + 速度 + 急停/复位 + 引擎状态 + 最近结果。
//
// ★ 点位执行 与 程序执行 两页共用同一份（逻辑全在 stores/exec.js）。
//   安全相关的 UI 只有一处实现，改判据时不可能漏掉其中一个页面。
import { computed } from "vue";
import Icon from "./Icon.vue";
import { useAuthStore } from "../stores/auth.js";
import { useExecStore } from "../stores/exec.js";
import { useRobotStore } from "../stores/robot.js";
import { useRcReadyStore } from "../stores/rcReady.js";

const auth = useAuthStore();
const exec = useExecStore();
const robot = useRobotStore();
const rc = useRcReadyStore();

// ★ 控制权限必须同时满足：有令牌 + 机器人已连接 + 遥测未冻结（F-05：读数不可信时不放行）
const canControl = computed(() => auth.controlActive && robot.connected && !robot.telemetryStale);

// ★ 需求：「已获得控制权限」这句话**只有一键就绪成功后**才显示。
//   拿到令牌只是"授权"，真正能动还得控制器处于就绪态（伺服上电+程序运行+AUTO/无报警/非急停）。
//   只写"已获得控制权限"会让人以为随时可下发，与真机实际状态脱节。
const granted = computed(() => canControl.value && rc.ready);

// ★ 审计修复 P1-D9：急停/复位**只看令牌**，不能被「机器人离线」（WS 连接态）禁掉。
//   原实现急停按钮跟其它写操作共用 canControl —— WS 断了的那一刻（恰恰是最可能
//   需要急停的时候）按钮变灰点不动；而后端急停本来就是本地标志位 + 尽力下发，
//   离线时也必须能按下（至少让引擎状态与前端一致）。
const canEstop = computed(() => auth.controlActive);

const estopText = computed(() => {
  const s = exec.estopState;
  if (!s) return "";
  return (s.stopped ? "急停锁定" : (s.moving ? "运动中" : "空闲"))
    + " · " + (s.mode === "real" ? "真实" : "模拟");
});
</script>

<template>
  <div class="card">
    <h3><Icon name="shield" :size="15" /> 控制权限
      <span class="h3-sub">{{ canControl ? auth.ttlText : "未就绪" }}</span>
    </h3>
    <div class="row">
      <span class="k">状态</span>
      <span class="v" :style="{ color: granted ? 'var(--ok)' : 'var(--warn)' }">
        {{ granted ? "已获得控制权限"
           : (!robot.connected ? "机器人离线"
              : (!canControl ? "未获得控制权限" : "已授权 · 待一键就绪")) }}
      </span>
    </div>
    <div class="btns">
      <button v-if="auth.controlActive" class="ctrl-release" @click="auth.logout()">
        <Icon name="power" :size="14" /> 释放控制
      </button>
      <button v-else class="primary" @click="auth.requestLogin && auth.requestLogin()">
        <Icon name="lock" :size="14" /> 请求控制
      </button>
      <button :disabled="!canControl" @click="exec.refreshState()">
        <Icon name="refresh" :size="14" /> 刷新状态
      </button>
    </div>
    <p v-if="!canControl" class="small">
      {{ !robot.connected ? "机器人未连接，请先连接机器人再获取控制权限。" : "连接真机执行前须先获取限时控制令牌；所有写操作受此门控。" }}
    </p>

    <!-- ★ 审计修复 P1-D8：执行前必须知道读数是不是"冻住的旧值"。
         WS 没断 ≠ 有新数据；真机执行时照着定格的姿态判断会出事。 -->
    <p v-if="robot.connected && robot.telemetryStale" class="pf-err">
      <Icon name="alert" :size="13" /> 遥测已冻结（超过 3 秒无新帧），读数不可信，请先排查后端采集
    </p>

    <hr class="sp-hr" />

    <div class="pf-row">
      <label>速度</label>
      <input type="range" min="5" max="100" v-model.number="exec.speed" />
      <span class="v" style="width:48px;text-align:right">{{ exec.speed }}%</span>
    </div>

    <p v-if="exec.blockReason" class="pf-err">
      <Icon name="alert" :size="13" /> {{ exec.blockReason }}
    </p>

    <!-- ★ 真空吸放：写 40135.Bit1/Bit2 触发位，控制器常驻服务程序 **210** 执行，不移动机器人。
         吸气=电平保持（不自动停，需按「停止吸气」关断）；停止吸气=立即关断（默认态）。
         ★ 状态由 store 锁存：吸气成功后持续显示"吸气中"，避免"阀开着却显示空闲"。
         ★ 「停止吸气」**不因 vacuumBusy 被禁用** —— 它是安全动作，
           哪怕上一发吸气请求还挂着，操作员也必须能立刻关阀。 -->
    <hr class="sp-hr" />
    <div class="vac-title">
      <Icon name="grip" :size="13" /> 真空吸放
      <span class="h3-sub" :class="exec.vacuumState === 'suck' ? 'on-suck' : ''">
        {{ exec.vacuumState === 'suck' ? '吸气中（保持）' : '已关断' }}
      </span>
    </div>
    <div class="btns" style="margin-top:6px">
      <button class="vac-suck" :class="{ 'is-on': exec.vacuumState === 'suck' }"
              :disabled="!canControl || exec.vacuumBusy"
              @click="exec.vacuum('suck')">
        <Icon name="download" :size="14" /> 吸气
      </button>
      <button class="vac-release" :disabled="!canControl"
              @click="exec.vacuum('release')">
        <Icon name="upload" :size="14" /> 停止吸气
      </button>
    </div>
    <p v-if="exec.vacuumErr" class="pf-err">
      <Icon name="alert" :size="13" /> {{ exec.vacuumErr }}
    </p>
    <p v-else class="small">
      吸气为保持型（不会自动停止），须按「停止吸气」关断。控制器须 AUTO 且常驻程序 210 运行中。
    </p>

    <div class="btns" style="margin-top:8px">
      <button class="rec-on" :disabled="!canEstop || exec.estopBusy"
              @click="exec.doEstop()">
        <Icon name="power" :size="14" /> 急停
      </button>
      <button :disabled="!canEstop || exec.estopBusy" @click="exec.resetEstop()">
        <Icon name="refresh" :size="14" /> 复位急停
      </button>
    </div>

    <div v-if="exec.estopState" class="row">
      <span class="k">引擎状态</span>
      <span class="v">{{ estopText }}</span>
    </div>
    <div v-if="exec.execState" class="exec-result" :class="exec.execState.ok ? 'ok' : 'err'">
      <Icon :name="exec.execState.ok ? 'check' : 'alert'" :size="13" />
      <span v-if="exec.execState.ok">
        已下发：目标 [{{ (exec.execState.target || []).join(", ") }}] · {{ exec.execState.mode }}
      </span>
      <span v-else>{{ exec.execState.error || exec.execState.message || "执行失败" }}</span>
    </div>
  </div>
</template>

<style scoped>
.ctrl-release { color: var(--warn); }
.ctrl-release:hover { border-color: var(--warn); }
.vac-title { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); margin-top: 4px; }
.vac-suck { color: var(--c-suck, #2f9e6f); border-color: var(--c-suck-line, #2f9e6f55); }
.vac-suck:hover:not(:disabled) { background: var(--c-suck, #2f9e6f); color: #fff; }
/* ★ 吸气是"保持型"执行器 —— 保持中时按钮必须看得出是"已engage"，
   否则截图给现场看，没人能一眼分辨"按过没生效"还是"正保持中"。 */
.vac-suck.is-on { background: var(--c-suck, #2f9e6f); color: #fff; border-color: var(--c-suck, #2f9e6f); }
.vac-release { color: var(--c-release, #d98a2b); border-color: var(--c-release-line, #d98a2b55); }
.vac-release:hover:not(:disabled) { background: var(--c-release, #d98a2b); color: #fff; }
.on-suck { color: var(--c-suck, #2f9e6f); }
</style>
