// =====================================================================
// Pinia store：安全围栏配置（持久化在后端 config/safety.json）+ 报警事件。
//
// 交互约定：
//   - 面板拖动 = 改本地 config + 立即推给 three（liveApply），不落盘；
//   - 点「保存」才 PUT 到后端；未保存时 dirty=true（按钮显示红点）；
//   - 「重置」拉后端出厂默认并立即应用；「撤销」回到上次已保存快照。
// =====================================================================
import { defineStore } from "pinia";
import { apiUrl } from "../config.js";
import { apiControl } from "../net/control.js";
import { useAuthStore } from "./auth.js";
import { setSafetyConfig, getCameraPose, applyCameraPose } from "../three/manager.js";
import { updateAlarm } from "../utils/alarm.js";
import { defaultSafety, normalizeSafetyConfig } from "../utils/safetyConfig.js";

// 兼容旧的导入路径（面板等曾从 store 里取默认配置）
export { defaultSafety, normalizeSafetyConfig };

const clone = (o) => JSON.parse(JSON.stringify(o));
const RANK = { safe: 0, warn: 1, danger: 2, hit: 3 };

// 报警事件冷却：同一区域同一状态 10s 内只记一条，防止在阈值边界反复刷库
const EVENT_COOLDOWN_MS = 10000;
const lastEventAt = {};

export const useSafetyStore = defineStore("safety", {
  state: () => ({
    config: null,        // 当前（可能被改动）的配置
    saved: null,         // 上次已保存快照，用于"撤销"
    loaded: false,
    dirty: false,
    busy: false,
    error: "",
    events: [],
    lastState: "safe",
    /**
     * 这份 lastState 量的是谁：
     *   "ghost" = 残影预演的**目标位姿**（"要去的地方会不会撞"）
     *   "robot" = 实体机的**当前位姿**（实时监控）
     * ★ 执行域据此区分：只有 "ghost" 才构成点位/示教/程序的拦截依据。
     */
    lastSource: "robot",
    last: { state: "safe", ratio: 1, clearance: 0, zoneId: "", zoneName: "" },
    appliedJson: "",
  }),
  getters: {
    zones: (s) => (s.config && s.config.zones) || [],
  },
  actions: {
    /** 启动时拉取配置并应用到 3D 场景。 */
    async load() {
      try {
        const r = await fetch(apiUrl("/safety"));
        if (!r.ok) throw new Error("HTTP " + r.status);
        // ★ 归一化：补齐后端/旧文件里缺失的子块（尤其是 ground.colors），
        //   否则"按状态取色"会全部回退成绿色 —— 地面垫报警永远是绿的。
        this.config = normalizeSafetyConfig(await r.json());
      } catch (e) {
        this.config = normalizeSafetyConfig({});
        this.error = "后端不可用，使用默认配置";
      }
      this.saved = clone(this.config);
      this.loaded = true;
      this.dirty = false;
      this.apply();
      // 启动时恢复上次保存的视角（只在 load 里做一次，改参数绝不动视角）
      if (this.config.camera) {
        applyCameraPose(this.config.camera.position, this.config.camera.target);
      }
      this.refreshEvents();
    },

    /**
     * 把当前 config 推给 three（不落盘）。任何编辑后都要调用。
     * ★ 绝不动相机：改参数必须保持当前视角。
     */
    apply() {
      if (!this.config) return;
      updateAlarm(this.config.alarm && this.config.alarm.sound ? this.lastState : "safe");
      // 内容没变就不重建围栏（拖动滑块时同一帧可能触发多次）
      const json = JSON.stringify(this.config);
      if (json !== this.appliedJson) {
        this.appliedJson = json;
        setSafetyConfig(this.config);
      }
    },

    /** 面板编辑入口：改完立即生效并标脏。 */
    touch(fn) {
      if (!this.config) return;
      if (fn) fn(this.config);
      this.dirty = true;
      this.apply();
    },

    /**
     * 统一处理写请求的响应。
     * ★ 阶段 4：改围栏配置需要管理员令牌 —— 401/403 时直接把登录弹窗顶起来，
     *   而不是只在面板里留一句看不懂的报错。@return 原始 Response（调用方自行判定）
     */
    _afterWrite(r) {
      if (r.status === 401 || r.status === 403) {
        const auth = useAuthStore();
        this.error = r.status === 403
          ? "该操作需要管理员权限（当前为操作员）"
          : "需要管理员权限：请先获取控制令牌";
        auth.requestLogin();
      }
      return r;
    },

    async save() {
      if (!this.config || this.busy) return;
      this.busy = true;
      this.error = "";
      // ★ 保存时自动带上当前视角：否则"先调好角度 → 再改参数保存"会把旧视角写回去，
      //   下次打开就被切回老角度（用户反馈的"改参数视角被切换"根源）。
      this._captureCamera(false);
      try {
        const r = await this._afterWrite(await apiControl("/safety", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ config: this.config }),
        }));
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          throw new Error((d && d.detail) || "保存失败");
        }
        const d = await r.json();
        this.config = d.config;
        this.saved = clone(this.config);
        this.dirty = false;
        this.apply();
        return true;
      } catch (e) {
        this.error = e.message || String(e);
        return false;
      } finally {
        this.busy = false;
      }
    },

    async reset() {
      if (this.busy) return;
      this.busy = true;
      try {
        const r = await this._afterWrite(
          await apiControl("/safety/reset", { method: "POST" }));
        if (!r.ok) throw new Error("恢复默认失败");
        const d = await r.json();
        this.config = normalizeSafetyConfig(d.config);
        this.saved = clone(this.config);
        this.dirty = false;
        this.apply();
      } catch (e) {
        this.error = e.message || String(e);
      } finally {
        this.busy = false;
      }
    },

    /** 把当前相机位姿写进 config（markDirty=true 时同时标脏并即时生效）。 */
    _captureCamera(markDirty = true) {
      if (!this.config) return;
      const p = getCameraPose();
      const cam = {
        position: p.position.map((v) => +v.toFixed(3)),
        target: p.target.map((v) => +v.toFixed(3)),
      };
      if (markDirty) {
        this.touch((c) => { c.camera = cam; });
      } else {
        this.config.camera = cam;
      }
    },

    /** 撤销未保存的改动。 */
    revert() {
      if (!this.saved) return;
      this.config = clone(this.saved);
      this.dirty = false;
      this.apply();
    },

    // ---------------- 区域管理 ----------------
    addZone() {
      this.touch((c) => {
        const n = c.zones.length + 1;
        const base = clone(defaultSafety().zones[0]);
        base.id = "z" + n;
        base.name = "区域" + n;
        // 新区域默认偏移一点，避免与原区域完全重合
        base.center = { x: 0, z: 0 };
        base.half = { x: 1.0 + n * 0.1, z: 1.0 + n * 0.1 };
        base.radius = 1.0 + n * 0.1;
        base.corners = [
          [-base.half.x, -base.half.z], [base.half.x, -base.half.z],
          [base.half.x, base.half.z], [-base.half.x, base.half.z],
        ];
        c.zones.push(base);
      });
    },

    removeZone(idx) {
      this.touch((c) => {
        if (c.zones.length <= 1) return;
        c.zones.splice(idx, 1);
      });
    },

    // ---------------- 相机视角 ----------------
    /** 保存当前视角并立即落盘（一步到位，避免"存了但没保存配置"）。 */
    async saveCamera() {
      this._captureCamera(false);
      await this.save();
    },
    resetCamera() {
      if (!this.config || !this.config.camera) return;
      const c = this.config.camera;
      applyCameraPose(c.position, c.target);
    },

    // ---------------- 报警事件 ----------------
    /** 状态变化时调用：变严重 / 恢复 才写库，避免每帧刷库。 */
    ingest(s) {
      const st = s && s.state ? s.state : "safe";
      const prev = this.lastState;
      this.lastState = st;
      this.lastSource = (s && s.source) || "robot";
      this.last = {
        state: st,
        source: this.lastSource,
        ratio: s ? s.ratio : 1,
        clearance: s ? s.clearance : 0,
        zoneId: s ? s.zoneId : "",
        zoneName: s ? s.zoneName : "",
      };
      if (this.config && this.config.alarm) {
        updateAlarm(this.config.alarm.sound ? st : "safe");
      }
      const worse = RANK[st] > RANK[prev] && RANK[st] >= RANK.danger;
      const recovered = RANK[prev] >= RANK.danger && RANK[st] < RANK.danger;
      if (!worse && !recovered) return;
      const key = `${s.zoneId || ""}|${recovered ? "clear" : st}`;
      const now = Date.now();
      if (lastEventAt[key] && now - lastEventAt[key] < EVENT_COOLDOWN_MS) return;
      lastEventAt[key] = now;
      // ★ 审计修复 P2：后端已给 POST /safety/events 加 require_control，
      //   这里补上控制令牌（auth 需在本作用域取，store 内没有现成变量）。
      const auth = useAuthStore();
      // ★ 未登录就跳过写库：GET 列表是公开的（时间线照常能看），
      //   但匿名访客每次报警都换一个 401 回来，控制台会被刷满。
      if (!auth.controlActive) return;
      fetch(apiUrl("/safety/events"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...auth.controlHeaders() },
        body: JSON.stringify({
          zone_id: s.zoneId || "", zone_name: s.zoneName || "",
          state: recovered ? "clear" : st,
          clearance: +(s.clearance || 0).toFixed(4),
          ratio: +(s.ratio || 0).toFixed(4),
        }),
      }).catch(() => {});
    },

    async refreshEvents(limit = 30) {
      try {
        const r = await fetch(apiUrl(`/safety/events?limit=${limit}`));
        if (r.ok) this.events = await r.json();
      } catch (e) { /* 静默 */ }
    },

    async clearEvents() {
      const r = await this._afterWrite(
        await apiControl("/safety/events", { method: "DELETE" }));
      if (r.ok) this.events = [];
    },
  },
});
