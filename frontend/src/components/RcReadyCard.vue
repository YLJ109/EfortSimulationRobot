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
import { useExecStore } from "../stores/exec.js";

const auth = useAuthStore();
const rc = useRcReadyStore();
const robot = useRobotStore();
const exec = useExecStore();     // 就绪/取消的结果写进运行日志（左下角面板）

// ★ 控制权限必须同时满足：有令牌 + 机器人已连接
const canControl = computed(() => auth.controlActive && robot.connected && !robot.telemetryStale);

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
  if (r.ok) {
    if (rc.lastAction === "cancel") {
      // ★ 取消就绪**成功**时后端只回 {ok,steps}、没有 summary ——
      //   原实现在这里掉进 `r.error || "就绪失败"`，成功却显示"就绪失败"。
      const off = (r.steps || []).some((s) => s.step === "servo_off" && s.ok);
      return off ? "取消就绪：程序已停止，伺服已下电" : "取消就绪：程序已停止（伺服保持上电）";
    }
    if (r.summary) {
      const s = r.summary;
      return "就绪完成：程序 " + s.prog + " 已挂起于 WAIT（伺服 " + (s.servo ? "上电" : "异常") + "）";
    }
    return "就绪完成";
  }
  return r.error || (rc.lastAction === "cancel" ? "取消就绪失败" : "就绪失败");
});

// ★ 需求：一键就绪 / 取消就绪 合并为**同一个按钮** —— 未就绪时绿色"一键就绪"，
//   已就绪时红色"取消就绪"，绿红区分，不再并排铺 6 个按钮。
const readyAction = computed(() => (rc.ready ? "cancel" : "ready"));
const readyDisabled = computed(() => {
  if (rc.busy) return true;
  // ★★ 修复"取消就绪后按钮点不动了"：
  //   「取消就绪」是**安全动作**（停程序 / 下电），任何情况下都必须可点 ——
  //   以前它和"发起就绪"共用一整套前置判据（canControl 含"机器人连接 + 遥测不冻结"），
  //   于是伺服一停/遥测一抖，按钮就整块变灰，既没法取消也没法再次就绪。
  //   现在只要求"持有控制令牌"这一条最低门槛。
  if (readyAction.value === "cancel") return !auth.controlActive;
  // 发起就绪：需要令牌 + 机器人在线（遥测可信）+ 双闸开启
  if (!auth.controlActive) return true;
  if (!rc.realEnabled) return true;
  if (!canControl.value) return true;
  // ★★ 不再因为"点动触发位仍为 1"禁用就绪（现场事故：某次点动异常退出把触发位留在 1，
  //   按钮被永久禁用 → "一键就绪点不了"，用户彻底没救）。
  //   后端 _ready() 的第一步就是**撤触发**（写 40135=0，纯撤销意图、不产生运动），
  //   所以这里只提示、不拦。
  return false;
});
function onReadyToggle() {
  // ★ 需求：一键就绪才做伺服上电；取消就绪要**一并下电**（servo_off=true）。
  //   所以取消就绪 = 停点动服务程序 + 伺服下电，恢复到"未上电/未就绪"。
  // ★ 结果写入运行日志（每一步成败都能看到，不用猜）。
  if (readyAction.value === "ready") {
    rc.doReady().then((ok) => {
      const bad = _failedSteps();
      exec.logLine(ok ? "ok" : "err",
        ok ? "一键就绪完成：撤触发 → 清报警 → 伺服上电 → 加载程序 200 → 运行挂起"
           : "一键就绪失败" + (bad ? "：" + bad : ""));
    });
  } else {
    rc.cancelReady(true).then((ok) => {
      const bad = _failedSteps();
      exec.logLine(ok ? "info" : "err",
        ok ? "取消就绪完成：程序已停止，伺服已下电"
           : "取消就绪失败" + (bad ? "：" + bad : ""));
    });
  }
}

/** 汇总后端返回里失败的步骤（用于日志/结果区给出可读原因）。 */
function _failedSteps() {
  const r = rc.lastResult;
  if (!r || !Array.isArray(r.steps)) return "";
  return r.steps.filter((s) => !s.ok)
    .map((s) => `${s.step}(${s.msg || "失败"})`).join("；");
}
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

      <!-- ★ 需求：只保留"一键就绪 / 取消就绪"这一个按钮。
           「刷新」「声明 AUTO」「声明远程」三个按钮已移除（不需要）：
           状态由 rcReady store 每 4 秒自动轮询，无需手动刷新；
           档位以控制器实际状态为准，不再由前端"声明"。 -->
      <div class="btns">
        <button class="ready-toggle" :class="readyAction === 'ready' ? 'go' : 'cancel'"
                :disabled="readyDisabled" @click="onReadyToggle">
          <Icon :name="rc.busy ? 'refresh' : (readyAction === 'ready' ? 'power' : 'close')" :size="14" />
          {{ rc.busy ? "处理中…" : (readyAction === "ready" ? "一键就绪" : "取消就绪") }}
        </button>
      </div>

      <p v-if="rc.jogTrig" class="small" style="margin-top:6px">
        检测到「点动触发位」仍为 1（上一发点动未收尾）：点「一键就绪」会**先撤销触发位**再继续。
      </p>

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

<style scoped>
/* ★ 绿(就绪)/红(取消) 切换按钮：整行宽、语义色随状态变化，一眼分清当前该按什么 */
.ready-toggle {
  flex: 1;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  font-weight: 700; letter-spacing: .3px;
}
.ready-toggle.go {
  background: var(--ok, #1fa74a); border-color: var(--ok, #1fa74a); color: #fff;
}
.ready-toggle.go:hover:not(:disabled) { filter: brightness(1.08); }
.ready-toggle.cancel {
  background: var(--err, #e02020); border-color: var(--err, #e02020); color: #fff;
}
.ready-toggle.cancel:hover:not(:disabled) { filter: brightness(1.08); }
.ready-toggle:disabled { opacity: .5; cursor: not-allowed; }
</style>
