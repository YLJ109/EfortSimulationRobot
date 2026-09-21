// =====================================================================
// Pinia store：录制 / 回放 / 录制库 CRUD + 导入导出。
// 迁移自原 main.js 的录制引擎，真实/模拟共用一个 recorder。
// =====================================================================
import { defineStore } from "pinia";
import { apiUrl } from "../config.js";
import { useRobotStore } from "./robot.js";
import { applyRobotPose } from "../three/manager.js";

export const useRecordingStore = defineStore("recording", {
  state: () => ({
    recorder: { state: "idle", source: "sim", frames: [], t0: 0, timer: null },
    player: { state: "idle", frames: null, t0: 0, offset: 0, name: "", progress: "" },
    list: [],
  }),
  getters: {
    recOn: (s) => s.recorder.state === "recording",
    playing: (s) => s.player.state === "playing",
    paused: (s) => s.player.state === "paused",
    hasFrames: (s) => !!(s.player.frames && s.player.frames.length),
  },
  actions: {
    currentJoints() {
      const robot = useRobotStore();
      if (this.recorder.source === "real") {
        return robot.latestPose ? robot.latestPose.slice() : [0, 0, 0, 0, 0, 0];
      }
      return robot.simQ.slice();
    },
    sampleFrame() {
      const t = Math.round(performance.now() - this.recorder.t0);
      const q = this.currentJoints();
      this.recorder.frames.push({ t, j1: q[0], j2: q[1], j3: q[2], j4: q[3], j5: q[4], j6: q[5] });
    },
    startRecording(source) {
      if (this.recorder.state === "recording") return;
      this.recorder.state = "recording";
      this.recorder.source = source;
      this.recorder.frames = [];
      this.recorder.t0 = performance.now();
      this.recorder.timer = setInterval(() => this.sampleFrame(), 50);
    },
    async stopRecording() {
      if (this.recorder.state !== "recording") return;
      clearInterval(this.recorder.timer);
      this.recorder.timer = null;
      const frames = this.recorder.frames;
      const source = this.recorder.source;
      this.recorder.state = "idle";
      if (!frames.length) return;
      const duration = frames[frames.length - 1].t;
      const stamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      const name = (source === "real" ? "真机录制 " : "模拟录制 ") + stamp;
      try {
        await fetch(apiUrl("/recordings"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, source, duration_ms: duration, frames }),
        });
        await this.refreshList();
      } catch (e) { /* 静默 */ }
    },

    // ---------- 回放 ----------
    lerpPose(frames, tMs) {
      if (tMs <= frames[0].t) return frames[0];
      const last = frames[frames.length - 1];
      if (tMs >= last.t) return last;
      let lo = 0, hi = frames.length - 1;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (frames[mid].t <= tMs) lo = mid; else hi = mid;
      }
      const a = frames[lo], b = frames[hi];
      const span = b.t - a.t || 1;
      const r = (tMs - a.t) / span;
      return {
        j1: a.j1 + (b.j1 - a.j1) * r, j2: a.j2 + (b.j2 - a.j2) * r,
        j3: a.j3 + (b.j3 - a.j3) * r, j4: a.j4 + (b.j4 - a.j4) * r,
        j5: a.j5 + (b.j5 - a.j5) * r, j6: a.j6 + (b.j6 - a.j6) * r,
      };
    },
    loadIntoPlayer(frames, name) {
      this.player.frames = frames;
      this.player.name = name || "";
      this.player.state = "idle";
      this.player.offset = 0;
      this.player.progress = "";
      if (frames && frames.length) this.applyCurrent();
    },
    /** 把当前播放进度对应的姿态推给 3D 场景。 */
    applyCurrent() {
      const p = this.player;
      if (!p.frames || !p.frames.length) return;
      const total = p.frames[p.frames.length - 1].t || 1;
      const elapsed = p.state === "playing" ? p.offset + (performance.now() - p.t0) : p.offset;
      const f = this.lerpPose(p.frames, Math.min(elapsed, total));
      const robot = useRobotStore();
      if (robot.activeView !== "rec") return;    // 单模型：只有本视图在前台才驱动姿态
      applyRobotPose([f.j1, f.j2, f.j3, f.j4, f.j5, f.j6]);
    },
    playbackTick() {
      const p = this.player;
      if (p.state !== "playing" || !p.frames || !p.frames.length) return;
      const elapsed = p.offset + (performance.now() - p.t0);
      const frame = this.lerpPose(p.frames, elapsed);
      const robot = useRobotStore();
      if (robot.activeView === "rec") {
        applyRobotPose([frame.j1, frame.j2, frame.j3, frame.j4, frame.j5, frame.j6]);
      }
      const total = p.frames[p.frames.length - 1].t || 1;
      p.progress = `${(elapsed / 1000).toFixed(1)} / ${(total / 1000).toFixed(1)} s`;
      if (elapsed >= total) { this.stopPlayback(); return; }
      requestAnimationFrame(() => this.playbackTick());
    },
    play() {
      if (!this.player.frames || !this.player.frames.length) return;
      this.player.state = "playing";
      this.player.t0 = performance.now();
      requestAnimationFrame(() => this.playbackTick());
    },
    pauseToggle() {
      const p = this.player;
      if (p.state === "playing") {
        p.offset += performance.now() - p.t0;
        p.state = "paused";
      } else if (p.state === "paused") {
        p.t0 = performance.now();
        p.state = "playing";
        requestAnimationFrame(() => this.playbackTick());
      }
    },
    stopPlayback() {
      this.player.state = "idle";
      this.player.offset = 0;
      this.player.progress = "";
    },

    // ---------- 录制库 CRUD ----------
    async refreshList() {
      try {
        const r = await fetch(apiUrl("/recordings"));
        this.list = await r.json();
      } catch (e) {
        this.list = [];
      }
    },
    async remove(item) {
      await fetch(apiUrl(`/recordings/${item.id}`), { method: "DELETE" });
      if (this.player.name === item.name) this.loadIntoPlayer(null, "");
      await this.refreshList();
    },
    async rename(item, name) {
      await fetch(apiUrl(`/recordings/${item.id}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      await this.refreshList();
    },
    download(item) {
      const a = document.createElement("a");
      a.href = apiUrl(`/recordings/${item.id}/export`);
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
    async loadDetail(item) {
      const r = await fetch(apiUrl(`/recordings/${item.id}`));
      const d = await r.json();
      this.loadIntoPlayer(d.frames || [], d.name);
      return d;
    },
    async importJson(payload) {
      await fetch(apiUrl("/recordings/import"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await this.refreshList();
    },
  },
});
