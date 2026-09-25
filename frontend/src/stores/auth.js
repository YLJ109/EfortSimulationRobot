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
  // ★ P1-E13（eslint no-empty）：隐私模式 / 禁用存储时 sessionStorage 会直接抛，
  //   令牌退化为"只在内存里"，登录照常可用 —— 不能因为存不下就打断登录。
  try { sessionStorage.setItem(LS_KEY, JSON.stringify(t)); } catch (e) { /* 存不下就只留内存 */ }
}
function clear() {
  try { sessionStorage.removeItem(LS_KEY); } catch (e) { /* 删不掉也不影响本次会话 */ }
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
      // ★ TTL 实时倒计时用
      _ttlTick: null,
      _ttlTickForceUpdate: 0,
    };
  },
  getters: {
    // ★ expiresAt === 0 = 不限时；有值则按时间判。
    controlActive: (s) => !!s.token && notExpired(s.expiresAt),
    /** 管理员令牌：改围栏配置 / 清审计日志 / 导备份这类高风险动作只对 admin 开放。 */
    isAdmin: (s) => !!s.token && notExpired(s.expiresAt) && s.role === "admin",
    roleLabel: (s) => (s.role === "admin" ? "管理员" : s.role === "operator" ? "操作员" : ""),
    /** 剩余秒数（不限时返回 -1，过期返回 0）。
     *  ★ 必须读一下 `_ttlTickForceUpdate`：它是 1s 心跳写的"哑变量"。
     *    不读它，本 getter 就没有任何响应式依赖会随时间变化（`Date.now()` 不是响应式），
     *    Pinia 永远不会重算 → 底部倒计时看起来"不动"。 */
    ttlSec: (s) => {
      void s._ttlTickForceUpdate;
      return s.expiresAt === 0 ? -1 : Math.max(0, Math.round((s.expiresAt - Date.now()) / 1000));
    },
    /** 格式化的剩余时间文本，供 UI 实时显示（同上：依赖 1s 心跳才会逐秒刷新）。 */
    ttlText: (s) => {
      void s._ttlTickForceUpdate;
      if (s.expiresAt === 0) return "不限时";
      const s2 = Math.max(0, Math.round((s.expiresAt - Date.now()) / 1000));
      if (s2 <= 0) return "已过期";
      const m = Math.floor(s2 / 60), ss = s2 % 60;
      return m > 0 ? `${m}分${ss}秒` : `${ss}秒`;
    },
  },
  actions: {
    /** 启动/停止 TTL 实时倒计时（页面可见时自动启动）。 */
    startTtlTick() {
      if (this._ttlTick) return;
      this._ttlTick = setInterval(() => {
        // 触发响应式更新：修改一个 dummy state 让 getter 重算
        this._ttlTickForceUpdate = Date.now();
      }, 1000);
    },
    stopTtlTick() {
      if (this._ttlTick) { clearInterval(this._ttlTick); this._ttlTick = null; }
    },
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
            this.startTtlTick();  // ★ 有效令牌时启动实时倒计时
          } else {
            this._clear();
            this.stopTtlTick();
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
        // ★ 全维度审查 F-04：角色兜底为最保守的 ""（操作员/管理员入口按后端实际回值显示）。
        //   原实现兜底 "admin" 是提权风险：一旦后端回了空/异常角色，前端就把用户当管理员、
        //   显示改配置/清审计等高危入口。真正的权限判定永远在后端（require_admin），
        //   这里只控制 UI 入口可见性，所以缺省必须收起而非放开。
        this.role = d.role || "";
        save({ token: d.token, expiresAt: this.expiresAt, role: this.role });
        this.startTtlTick();  // ★ 启动实时倒计时
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
      this.stopTtlTick();  // ★ 停止实时倒计时
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
      this.stopTtlTick();  // ★ 停止实时倒计时
      this.error = "控制令牌已失效（后端重启或口令变更），请重新验证";
    },
  },
});
