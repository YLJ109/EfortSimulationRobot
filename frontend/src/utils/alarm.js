// =====================================================================
// 声音报警：Web Audio 蜂鸣（无外部素材，浏览器原生）。
// 分级：danger = 1Hz 中音短鸣；hit = 3Hz 高音急促鸣。safe/warn 不发声。
// =====================================================================
let ctx = null;
let enabled = false;
let timer = null;
let current = "safe";

function audio() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
  }
  if (ctx.state === "suspended") ctx.resume();
  return ctx;
}

function blip(freq, dur, vol) {
  const ac = audio();
  if (!ac) return;
  const osc = ac.createOscillator();
  const g = ac.createGain();
  osc.type = "square";
  osc.frequency.value = freq;
  g.gain.setValueAtTime(0.0001, ac.currentTime);
  g.gain.exponentialRampToValueAtTime(vol, ac.currentTime + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + dur);
  osc.connect(g);
  g.connect(ac.destination);
  osc.start();
  osc.stop(ac.currentTime + dur + 0.02);
}

function stopTimer() {
  if (timer) { clearInterval(timer); timer = null; }
}

function schedule() {
  stopTimer();
  if (!enabled || current === "safe" || current === "warn") return;
  const period = current === "hit" ? 330 : 1000;
  const freq = current === "hit" ? 1180 : 760;
  const vol = current === "hit" ? 0.16 : 0.09;
  blip(freq, 0.12, vol);
  timer = setInterval(() => blip(freq, 0.12, vol), period);
}

/** 开关声音报警。 */
export function setAlarmEnabled(v) {
  enabled = !!v;
  if (enabled) schedule(); else stopTimer();
}

export function getAlarmEnabled() { return enabled; }

/** 状态变化时调用（内部去重，重复调用同一状态不会重启定时器）。 */
export function updateAlarm(state) {
  if (state === current) return;
  current = state;
  schedule();
}
