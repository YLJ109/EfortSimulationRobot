<script setup>
// =====================================================================
// 真机链路就绪卡：控制器寄存器状态一目了然 + 一键就绪 + 档位声明。
//
//   回答现场最常问的两个问题：
//     ① "为什么点不动？" —— 档位不在 AUTO/远程、伺服没上电、报警没清、
//        程序没加载，这里逐行给出答案，不再让用户对着 409 猜。
//     ② "怎么让它能动？" —— 一键就绪：清报警 → 伺服上电 → 加载点动服务
//        程序（现场=200/JOGSVC）→ 运行挂起于 WAIT。全程不写目标角区，
//        不产生任何运动。
//
// 数据来自 stores/rcReady.js（/api/rc-status 4s 轮询，公开只读接口，无需令牌）。
// 档位声明复用 link store 的唯一通道，绝不另写一份 /control/run-mode 调用。
// =====================================================================
import { computed, onMounted, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";
import { useAuthStore } from "../stores/auth.js";
import { useRcReadyStore } from "../stores/rcReady.js";
import { useRobotStore } from "../stores/robot.js";

const auth = useAuthStore();
const rc = useRcReadyStore();
const robot = useRobotStore();

// ★ 控制权限必须同时满足：有令牌 + 机器人已连接
const canControl = computed(() => auth.controlActive && robot.connected);

onMounted(() => rc.start());
onBeforeUnmount(() => rc.stop());

/** 状态行：ok=true 绿（允许），false 红（阻碍点动）。 */
const rows = computed(() => {
  if (!rc.ok || !rc.bits) return [];
  const b = rc.bits;
  return [
    { k: "双闸", v: rc.realEnabled ? "已开启" : "未开启", ok: rc.realEnabled },
    { k: "档位", v: rc.modeText, ok: rc.joggable },
    { k: "伺服", v: b.servo ? "上电" : "未上电", ok: !!b.servo },
    { k: "报警", v: b.alarm ? ("码 " + rc.snap.alarm1 + "/" + rc.snap.alarm2) : "无", ok: !b.alarm },
    { k: "急停", v: b.estop ? "按下" : "未按下", ok: !b.estop },
    { k: "程序", v: rc.progText, ok: !!(b.prog_loaded && b.run) },
    { k: "点动", v: rc.jogTrig ? "上一发收尾中" : "触发位空闲", ok: !rc.jogTrig },
  ];
});

const badgeText = computed(() => {
  if (!rc.ok) return "读取中…";
  return rc.ready ? "就绪 · 可点动" : "未就绪";
});

const resultText = computed(() => {
  const r = rc.lastResult;
  if (!r) return "";
  if (r.ok && r.summary) {
    const s = r.summary;
    return "就绪完成：程序 " + s.prog + " 已挂起于 WAIT（伺服 " + (s.servo ? "上电" : "异常") + "）";
  }
  return r.error || "就绪失败";
});
</script>

<template>
  <div class="card">
    <h3><Icon name="plug" :size="15" /> 真机链路
      <span class="h3-sub" :style="{ color: rc.ready ? 'var(--ok)' : 'var(--warn)' }">
        {{ badgeText }}
      </span>
    </h3>

    <template v-if="rc.ok">
      <div v-for="row in rows" :key="row.k" class="row">
        <span class="k">{{ row.k }}</span>
        <span class="v" :style="{ color: row.ok ? 'var(--ok)' : 'var(--err)' }">{{ row.v }}</span>
      </div>

      <div v-if="!rc.realEnabled" class="small" style="margin-top:6px">
        真实下发未开启：后端处于模拟模式，点动只走仿真。开双闸见系统自检 real_write 项。
      </div>

      <div class="btns">
        <button class="primary" :disabled="!canControl || rc.busy || !rc.realEnabled || rc.jogTrig"
                @click="rc.doReady()">
          <Icon name="power" :size="14" /> {{ rc.busy ? "就绪中…" : "一键就绪" }}
        </button>
        <button :disabled="rc.busy" @click="rc.load()">
          <Icon name="refresh" :size="14" /> 刷新
        </button>
      </div>

      <div v-if="!rc.joggable" class="btns" style="margin-top:6px">
        <button :disabled="rc.busy" @click="rc.claimMode('AUTO')">声明 AUTO</button>
        <button :disabled="rc.busy" @click="rc.claimMode('REMOTE')">声明远程</button>
      </div>

      <p v-if="!canControl" class="small">
        {{ !robot.connected ? "机器人未连接，请先连接机器人。" : "一键就绪会写控制器寄存器：需先请求控制权限。" }}
      </p>
    </template>

    <p v-else class="small">{{ rc.error || "正在读取控制器状态…" }}</p>

    <div v-if="resultText" class="exec-result" :class="rc.lastResult.ok ? 'ok' : 'err'">
      <Icon :name="rc.lastResult.ok ? 'check' : 'alert'" :size="13" /> {{ resultText }}
    </div>
  </div>
</template>
