// =====================================================================
// Pinia store：链路自检（/api/system/guide）的唯一轮询者。
//
// ★ 为什么必须收敛成一个 store：
//   底栏状态灯和「引导条」看的是同一份自检数据。如果各拉各的，就会出现
//   "状态灯是绿的、引导条还挂着红色未通过项"这种自相矛盾的界面 ——
//   现场看到两个结论，只会两个都不信。所以这里只有一路请求、一份状态。
//
// 提供：三盏灯（pills/tone）、引导条（primary）、档位声明动作。
// 相机/机器人各自的"重连"仍委托给既有 store（camera store）与既有接口，
// 不在本 store 里重写一份。
// =====================================================================
import { defineStore } from "pinia";
import { apiControl } from "../net/control.js";
import {
  allPills, stripTone, worstPill,
} from "../linkview.js";

/** 轮询周期。5 秒：够快地反映插拔网线/开关相机，又不会把后端打满。 */
export const LINK_POLL_MS = 5000;

let timer = null;

export const useLinkStore = defineStore("link", {
  state: () => ({
    guide: null,        // /system/guide 原始快照
    error: "",          // 自检接口本身不可用时的原因
    at: 0,              // 最近一次成功的时间戳
    busy: false,        // 声明档位中
    actionMsg: "",      // 详情面板里的一行反馈
    claimError: "",
  }),
  getters: {
    links: (s) => (s.guide && s.guide.links) || null,
    /** 三盏灯（机器人 / 摄像头 / 示教器档位）。★ 任何情况下都返回三个元素。 */
    pills() {
      return allPills(this.links);
    },
    robot: (s) => (s.guide && s.guide.links && s.guide.links.robot) || null,
    camera: (s) => (s.guide && s.guide.links && s.guide.links.camera) || null,
    runMode: (s) => (s.guide && s.guide.links && s.guide.links.run_mode) || null,
    /** 底栏整体色调：三盏灯里最差的那一档。 */
    tone() { return stripTone(this.pills); },
    /** 第一个非 ok 的灯（没有就返回 null = 全部正常）。 */
    attention() { return worstPill(this.pills); },
    /** 引导条主提示（有未通过项才有）。 */
    primary: (s) => (s.guide && s.guide.primary) || null,
    hasToken: (s) => !!(s.guide && s.guide.has_token),
    ready: (s) => !!s.guide && !s.guide.primary,
    /** 自检是否过期（后端挂了也别一直显示旧绿）。 */
    stale: (s) => !!s.at && Date.now() - s.at > LINK_POLL_MS * 3,
  },
  actions: {
    async load() {
      try {
        const r = await apiControl("/system/guide");
        if (!r.ok) {
          this.error = "自检接口返回 " + r.status;
          return;
        }
        this.guide = await r.json();
        this.error = "";
        this.at = Date.now();
      } catch (e) {
        this.error = "后端不可用（" + (e && e.message ? e.message : "网络错误") + "）";
      }
    },
    start() {
      if (timer) return;
      this.load();
      timer = setInterval(() => this.load(), LINK_POLL_MS);
    },
    stop() {
      if (timer) { clearInterval(timer); timer = null; }
    },
    /** 立即刷一次（做完动作后调用，不等下一个轮询周期）。 */
    async refresh() { await this.load(); },

    /** 声明示教器档位（需控制令牌）。 */
    async claimMode(mode) {
      this.busy = true;
      this.claimError = "";
      this.actionMsg = "";
      try {
        const r = await apiControl("/control/run-mode", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.claimError = d.message || d.detail || ("声明失败（" + r.status + "）");
          return false;
        }
        this.actionMsg = "已声明为 " + (d.label || mode);
        await this.load();
        return true;
      } catch (e) {
        this.claimError = "声明失败：" + (e && e.message ? e.message : "网络错误");
        return false;
      } finally {
        this.busy = false;
      }
    },

    /** 撤销档位声明（回到"未确认"）。 */
    async clearMode() {
      this.busy = true;
      this.claimError = "";
      try {
        const r = await apiControl("/control/run-mode", { method: "DELETE" });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          this.claimError = d.message || d.detail || ("撤销失败（" + r.status + "）");
          return false;
        }
        this.actionMsg = "已撤销声明（回到未确认）";
        await this.load();
        return true;
      } catch (e) {
        this.claimError = "撤销失败：" + (e && e.message ? e.message : "网络错误");
        return false;
      } finally {
        this.busy = false;
      }
    },

    /** 重新连接控制器（需控制令牌）。 */
    async reconnect() {
      this.busy = true;
      this.actionMsg = "";
      try {
        const r = await apiControl("/system/reconnect", { method: "POST" });
        const d = await r.json().catch(() => ({}));
        if (r.status === 401 || r.status === 403) {
          this.actionMsg = "需要控制权限才能重连";
          return false;
        }
        // 成功就一句话：现场只要知道"通没通"，链路细节由上面的状态行承担。
        this.actionMsg = d.connected ? "连接成功" : "连接失败，仍处于模拟/离线";
        await this.load();
        return !!d.connected;
      } catch (e) {
        this.actionMsg = "连接失败：" + (e && e.message ? e.message : "网络错误");
        return false;
      } finally {
        this.busy = false;
      }
    },
  },
});
