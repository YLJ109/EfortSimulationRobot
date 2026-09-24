// =====================================================================
// Pinia store：控制权限鉴权（阶段 1；阶段 7 起支持不限时令牌）。
// 管理员密码 → 控制令牌（内存 + sessionStorage，刷新不丢；后端重启即失效）。
// 所有控制类请求通过 net/control.js 的 apiControl 自动带 X-Control-Token。
//
// ★ 时长语义（与后端 auth.py 对齐）：
//   expiresAt === 0 表示**不限时**（后端 TTL=0 时签发，expires_at=null → 这里存 0）；
//   其余值为毫秒时间戳，过期自动失效。
// =====================================================================
import { defineStore } from "pinia";
import { apiUrl } from "../config.js";

const LS_KEY = "efort.auth";

/** 判断令牌记录是否仍然有效：不限时(0)或未过期。 */
function notExpired(expiresAt) {
  return expiresAt === 0 || expiresAt > Date.now();
}

function loadToken() {
  try {
    const t = JSON.parse(sessionStorage.getItem(LS_KEY) || "null");
    return t && t.token && notExpired(t.expiresAt) ? t : null;
  } catch (e) {
    return null;
  }
}
function save(t) {
  try { sessionStorage.setItem(LS_KEY, JSON.stringify(t)); } catch (e) {}
}
function clear() {
  try { sessionStorage.removeItem(LS_KEY); } catch (e) {}
}

export const useAuthStore = defineStore("auth", {
  state: () => {
    const t = loadToken();
    return {
      token: t ? t.token : "",
      expiresAt: t ? t.expiresAt : 0,
      // 角色：admin | operator | ""。后端 /auth/login 与 /auth/status 都会回。
      // ★ 这里只用于「决定显示哪些入口」，真正的权限判定永远在后端（require_control /
      //   require_admin）—— 前端藏起来只是为了不让现场看到一片全灰的按钮。
      role: t && t.role ? t.role : "",
      // 后端启用了哪些角色（操作员未配口令时禁用）。用于提示"这个密码能拿到什么权限"。
      rolesEnabled: { admin: true, operator: false },
      required: true,
      busy: false,
      error: "",
      uiLogin: false,     // 登录弹窗显隐（任意视图都可触发）
    };
  },
  getters: {
    // ★ expiresAt === 0 = 不限时；有值则按时间判。
    controlActive: (s) => !!s.token && notExpired(s.expiresAt),
    /** 管理员令牌：改围栏配置 / 清审计日志 / 导备份这类高风险动作只对 admin 开放。 */
    isAdmin: (s) => !!s.token && notExpired(s.expiresAt) && s.role === "admin",
    roleLabel: (s) => (s.role === "admin" ? "管理员" : s.role === "operator" ? "操作员" : ""),
    ttlSec: (s) => (s.expiresAt === 0 ? -1 : Math.max(0, Math.round((s.expiresAt - Date.now()) / 1000))),
    ttlText: (s) => {
      if (s.expiresAt === 0) return "不限时";
      const s2 = Math.max(0, Math.round((s.expiresAt - Date.now()) / 1000));
      if (s2 <= 0) return "";
      const m = Math.floor(s2 / 60), ss = s2 % 60;
      return m > 0 ? `${m}分${ss}秒` : `${ss}秒`;
    },
  },
  actions: {
    /** 控制类请求头（无令牌时返回空对象，由后端判 401）。 */
    controlHeaders() {
      return this.token ? { "X-Control-Token": this.token } : {};
    },
    async fetchStatus() {
      try {
        const r = await fetch(apiUrl("/auth/status"), { headers: this.controlHeaders() });
        if (r.ok) {
          const d = await r.json();
          this.required = !!d.required;
          if (d.roles_enabled) this.rolesEnabled = d.roles_enabled;
          if (d.control_active) {
            this.role = d.role || "";
          } else {
            this._clear();
          }
        }
      } catch (e) { /* 后端不可用时静默 */ }
    },
    /** 打开登录弹窗（任意视图触发控制权限申请）。 */
    requestLogin() {
      this.error = "";
      this.uiLogin = true;
    },
    closeLogin() {
      this.uiLogin = false;
    },
    _clear() {
      this.token = "";
      this.expiresAt = 0;
      this.role = "";
      clear();
    },
    /**
     * 令牌被后端判定为无效（重启后端 / 改口令 / 过期）时立即收回。
     * ★ 为什么必须清：后端重启后进程内存里的令牌表是空的，本地那份已经死了。
     *   不清的话 controlActive 仍为 true → 每次进执行页都白发一批请求换回 401，
     *   控制台刷满 Unauthorized，用户还以为"有权限但用不了"。
     */
    invalidate() {
      if (!this.token) return;
      this._clear();
      this.error = "控制令牌已失效（后端重启或口令变更），请重新验证";
    },
    async login(password) {
      this.busy = true;
      this.error = "";
      try {
        const r = await fetch(apiUrl("/auth/login"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password }),
        });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          this.error = d.detail || "登录失败";
          return false;
        }
        const d = await r.json();
        this.token = d.token;
        // ★ 后端 ttl=0 时 expires_at=null → 存 0（不限时）；否则存毫秒时间戳。
        this.expiresAt = d.expires_at ? d.expires_at * 1000 : 0;
        this.role = d.role || "admin";     // 后端一定回 role；兜底按 admin 以免误藏入口
        save({ token: d.token, expiresAt: this.expiresAt, role: this.role });
        return true;
      } catch (e) {
        this.error = e.message || "登录失败";
        return false;
      } finally {
        this.busy = false;
      }
    },
    logout() {
      if (this.token) {
        fetch(apiUrl("/auth/logout"), {
          method: "POST",
          headers: this.controlHeaders(),
        }).catch(() => {});
      }
      this._clear();
    },
  },
});
