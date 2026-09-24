// 控制类请求封装：自动附带管理员控制令牌（X-Control-Token）。
// 未持令牌时后端返回 401。监控类请求仍用普通 fetch（见 config.js 的 apiUrl）。
//
// ★ 注意：本函数**不做**"无令牌就拦下"的统一判断 —— 同一个 apiControl 也承载
//   若干**公开**接口（如 GET /api/settings 走的是 optional_control），一刀切会把
//   它们一起挡掉。需要"未授权就别发请求"的调用点请自行判 auth.controlActive
//   （见 stores/exec.js 的 loadFiles、components/EventsView.vue 的 loadVersions）。
import { apiUrl } from "../config.js";
import { useAuthStore } from "../stores/auth.js";

export async function apiControl(url, opts = {}) {
  const auth = useAuthStore();
  const headers = Object.assign({}, opts.headers, auth.controlHeaders());
  const r = await fetch(apiUrl(url), { ...opts, headers });
  // ★ 后端重启 / 改口令后本地令牌已死：立刻收回。
  //   不清的话 controlActive 仍为 true，每次进执行页都会白发一批请求换回 401，
  //   控制台刷满 Unauthorized，用户还以为"有权限但用不了"。
  if (r.status === 401) auth.invalidate();
  return r;
}