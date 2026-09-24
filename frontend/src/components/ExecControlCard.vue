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

const auth = useAuthStore();
const exec = useExecStore();
const robot = useRobotStore();

// ★ 控制权限必须同时满足：有令牌 + 机器人已连接
const canControl = computed(() => auth.controlActive && robot.connected);

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
      <span class="v" :style="{ color: canControl ? 'var(--ok)' : 'var(--warn)' }">
        {{ canControl ? "已获得控制权限" : (!robot.connected ? "机器人离线" : "未获得控制权限") }}
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

    <hr class="sp-hr" />

    <div class="pf-row">
      <label>速度</label>
      <input type="range" min="1" max="100" v-model.number="exec.speed" />
      <span class="v" style="width:48px;text-align:right">{{ exec.speed }}%</span>
    </div>

    <p v-if="exec.blockReason" class="pf-err">
      <Icon name="alert" :size="13" /> {{ exec.blockReason }}
    </p>

    <div class="btns" style="margin-top:8px">
      <button class="rec-on" :disabled="!canControl || exec.estopBusy"
              @click="exec.doEstop()">
        <Icon name="power" :size="14" /> 急停
      </button>
      <button :disabled="!canControl || exec.estopBusy" @click="exec.resetEstop()">
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
</style>
