// =====================================================================
// Pinia store：系统设置（/api/settings*）
//
// 设计要点：
//   1. **草稿与生效值分离**：界面上的输入改的是 `draft`，点「保存」才 PUT。
//      不这么做的话，用户拖动一个数字框就会实时改配置（甚至触发断线重连）。
//   2. **来源标注**：每个字段带 `source`（yaml / settings / env / derived / default），
//      现场最常问的就是"这个值到底哪来的、我改了为什么没用" —— 直接标在字段上。
//   3. 保存与生效**分两步**：保存只落盘 + 清缓存；断开/重连控制器由「立即生效」触发。
//      合在一起时，地址填错会表现成"保存一下就掉线了"，分不清是保存还是网线。
// =====================================================================
import { defineStore } from "pinia";
import { apiControl } from "../net/control.js";

export const useSettingsStore = defineStore("settings", {
  state: () => ({
    groups: [],
    role: "",
    applyOrder: ["live", "reload", "reconnect", "restart"],
    overlayKeys: [],
    overridden: 0,
    runtime: null,
    live: null,

    /** 未保存的改动：{ 配置键: 新值 } */
    draft: {},

    loading: false,
    saving: false,
    testing: false,
    error: "",
    /** 上一次保存的结果：{ changed, apply, needs_restart, note } */
    result: null,
    /** 上一次生效动作的结果 */
    applyResult: null,
    /** 连接测试结果 */
    testResult: null,
    loadedAt: 0,
  }),

  getters: {
    /** 平铺后的字段索引（key → 字段描述），供组件查 label/admin/type。 */
    fieldMap: (s) => {
      const m = {};
      for (const g of s.groups) for (const f of g.fields) m[f.key] = { ...f, groupId: g.id, groupLabel: g.label };
      return m;
    },
    /** 草稿里与生效值不同的键。 */
    dirtyKeys(s) {
      const m = this.fieldMap;
      return Object.keys(s.draft).filter((k) => {
        const f = m[k];
        if (!f) return false;
        return JSON.stringify(s.draft[k]) !== JSON.stringify(f.value);
      });
    },
    dirtyCount() { return this.dirtyKeys.length; },
    hasChanges() { return this.dirtyCount > 0; },
    /** 当前令牌能不能改管理员项。 */
    isAdmin: (s) => s.role === "admin",
    /** 草稿里改动的项涉及的生效方式（最重的那个决定按钮文案）。 */
    pendingApply() {
      const m = this.fieldMap;
      const out = [];
      for (const k of this.dirtyKeys) {
        const a = (m[k] || {}).apply || "reload";
        if (!out.includes(a)) out.push(a);
      }
      return out.sort((x, y) => this.applyOrder.indexOf(x) - this.applyOrder.indexOf(y));
    },
    /** 改动是否包含"必须重启后端"的项。 */
    pendingRestart() { return this.pendingApply.includes("restart"); },
    /** 被环境变量顶掉、界面上改不了的项（要单独提示，否则用户会反复改）。 */
    envLocked(s) {
      return s.groups.flatMap((g) => g.fields).filter((f) => f.source === "env");
    },
  },

  actions: {
    async load() {
      this.loading = true;
      this.error = "";
      try {
        const r = await apiControl("/settings");
        const d = await r.json();
        if (!r.ok) {
          this.error = d.message || d.detail || ("读取配置失败（" + r.status + "）");
          return false;
        }
        this.groups = d.groups || [];
        this.role = d.role || "";
        this.applyOrder = d.apply_order || this.applyOrder;
        this.overlayKeys = d.overlay_keys || [];
        this.overridden = d.overridden || 0;
        this.runtime = d.runtime || null;
        this.draft = {};                 // 重新拉取 = 丢弃草稿（保存后也会走这里）
        this.loadedAt = Date.now();
        return true;
      } catch (e) {
        this.error = "读取配置失败：" + (e && e.message ? e.message : "网络错误");
        return false;
      } finally {
        this.loading = false;
      }
    },

    async loadLive() {
      try {
        const r = await apiControl("/settings/live");
        const d = await r.json();
        if (r.ok) this.live = d;
        return !!r.ok;
      } catch (e) {
        return false;
      }
    },

    /** 改草稿。传 null = 该字段恢复出厂值（PUT 时会带 null 过去）。 */
    setValue(key, value) {
      this.draft[key] = value;
      this.result = null;
    },
    /** 撤销某个字段的草稿改动。 */
    discard(key) { delete this.draft[key]; },
    discardAll() { this.draft = {}; this.result = null; },

    /** 某个字段当前显示的"草稿值"（没改过就是生效值）。 */
    draftOf(key) {
      if (Object.prototype.hasOwnProperty.call(this.draft, key)) return this.draft[key];
      const f = this.fieldMap[key];
      return f ? f.value : undefined;
    },

    async save() {
      if (!this.hasChanges) {
        this.result = { changed: [], apply: [], note: "没有需要保存的改动" };
        return true;
      }
      this.saving = true;
      this.error = "";
      try {
        const payload = {};
        for (const k of this.dirtyKeys) payload[k] = this.draft[k];
        const r = await apiControl("/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values: payload }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.error = d.message || d.detail || ("保存失败（" + r.status + "）");
          return false;
        }
        this.result = d;
        await this.load();                // 重新拉：草稿清空，来源标注刷新
        await this.loadLive();
        return true;
      } catch (e) {
        this.error = "保存失败：" + (e && e.message ? e.message : "网络错误");
        return false;
      } finally {
        this.saving = false;
      }
    },

    /** 立即生效：reload（重读配置 + 清缓存）与 reconnect（断连重连控制器）。 */
    async apply(actions) {
      this.saving = true;
      this.error = "";
      try {
        const r = await apiControl("/settings/apply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ actions: actions || [] }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.error = d.message || d.detail || ("生效失败（" + r.status + "）");
          return false;
        }
        this.applyResult = d;
        await this.loadLive();
        return true;
      } catch (e) {
        this.error = "生效失败：" + (e && e.message ? e.message : "网络错误");
        return false;
      } finally {
        this.saving = false;
      }
    },

    /** 连通性测试。可带临时地址（不保存也能试）。 */
    async testConnection(target, host, port) {
      this.testing = true;
      this.testResult = null;
      try {
        const r = await apiControl("/settings/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target, host: host || null, port: port || null }),
        });
        const d = await r.json().catch(() => ({}));
        this.testResult = r.ok ? d : { ok: false, target, message: d.message || d.detail || ("测试失败（" + r.status + "）") };
        return this.testResult;
      } catch (e) {
        this.testResult = { ok: false, target, message: "测试失败：" + (e && e.message ? e.message : "网络错误") };
        return this.testResult;
      } finally {
        this.testing = false;
      }
    },

    /** ★ 重置所有参数（清空覆盖层 → 全部回到 robot.yaml 出厂值）。不可撤销。 */
    async resetAll() {
      this.saving = true;
      this.error = "";
      try {
        const r = await apiControl("/settings/reset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.error = d.message || d.detail || ("重置失败（" + r.status + "）");
          return false;
        }
        this.result = d;
        await this.load();
        await this.loadLive();
        return true;
      } catch (e) {
        this.error = "重置失败：" + (e && e.message ? e.message : "网络错误");
        return false;
      } finally {
        this.saving = false;
      }
    },

    async changePassword(role, current, password) {
      this.saving = true;
      this.error = "";
      try {
        const r = await apiControl("/settings/password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role, current, password }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.error = d.message || d.detail || ("修改口令失败（" + r.status + "）");
          return false;
        }
        this.applyResult = d;
        return true;
      } catch (e) {
        this.error = "修改口令失败：" + (e && e.message ? e.message : "网络错误");
        return false;
      } finally {
        this.saving = false;
      }
    },
  },
});
