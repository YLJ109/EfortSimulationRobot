// =====================================================================
// 声音报警：委托给统一播报中心 services/announcer.js。
//
// 旧行为（方波蜂鸣）已升级为"分级音效 + 中文语音"，但对外 API 保持不变，
// 这样 SafetyPanel 的「声音」复选框等既有调用点无需改动。
// =====================================================================
import { annCfg, fenceAlarm, setConfig } from "../services/announcer.js";

let enabled = false;

/** 开关声音报警（对应围栏配置 alarm.sound）。 */
export function setAlarmEnabled(v) {
  enabled = !!v;
  setConfig({ sound: enabled });
  if (!enabled) fenceAlarm("safe");      // 关声音时立即停掉循环报警
}

export function getAlarmEnabled() { return !!(enabled && annCfg.value.sound); }

/** 状态变化时调用（内部去重，重复调用同一状态不会重新播报）。 */
export function updateAlarm(state) {
  fenceAlarm(enabled ? state : "safe");
}
