// =====================================================================
// Pinia store：机器人状态（元数据 / 限位 / 实时姿态 / 连接 / 模拟关节）
// 纯数据层，不直接操作 Three.js（由各视图组件 watch 后应用到模型）。
// =====================================================================
import { defineStore } from "pinia";
import { apiUrl } from "../config.js";
import { connectWs } from "../net/ws.js";
import { initRobots, loadOfficialModel } from "../three/manager.js";

export const DEFAULT_LIMITS = [
  { name: "J1", min: -170, max: 170 },
  { name: "J2", min: -170, max: 90 },
  { name: "J3", min: -85, max: 150 },
  { name: "J4", min: -180, max: 180 },
  { name: "J5", min: -115, max: 115 },
  { name: "J6", min: -360, max: 360 },
];

export const useRobotStore = defineStore("robot", {
  state: () => ({
    meta: null,
    limits: DEFAULT_LIMITS.map((l) => ({ ...l })),
    latestPose: null,   // [j1..j6]（度）
    latestTcp: null,    // {x,y,z}（mm）
    simulated: false,
    connected: false,
    simQ: [0, 0, 0, 0, 0, 0],
    selIdx: 0,
    activeView: "live",   // live | sim | rec | cam
    localDemo: false,     // true = 前端本地演示（离线跳舞）接管姿态，忽略远端模拟姿态
  }),
  getters: {
    axes: (s) => (s.meta && s.meta.axes) || ["J1", "J2", "J3", "J4", "J5", "J6"],
    robotName: (s) => (s.meta && s.meta.robot) || "ER8-700H",
    tcp: (s) => s.latestTcp || { x: 0, y: 0, z: 0 },
    calibrationPending: (s) => !!(s.meta && s.meta.dh_calibration_pending),
  },
  actions: {
    /** 拉取元数据并初始化三套机器人模型。 */
    async loadMeta() {
      try {
        const r = await fetch(apiUrl("/meta"));
        if (r.ok) this.meta = await r.json();
      } catch (e) {
        /* 后端不可用时用默认值兜底 */
      }
      const dh = this.meta && this.meta.dh && this.meta.dh.joints &&
        this.meta.dh.joints.length === 6 ? this.meta.dh : null;
      const mounting = (this.meta && this.meta.mounting) || "floor";
      if (this.meta && this.meta.joint_limits && this.meta.joint_limits.length === 6) {
        this.limits = this.meta.joint_limits;
      }
      initRobots(dh, mounting);
      loadOfficialModel();
    },
    setStatus(ok) {
      this.connected = ok;
    },
    applyPose(p) {
      this.simulated = !!p.simulated;
      this.connected = true;
      // 本地演示（离线跳舞）期间：只跟随连接状态，姿态一律由本地动画驱动，
      // 避免"远端模拟姿态"与"本地跳舞"两个写者互相抢写 → 动作抖动。
      if (this.localDemo) return;
      this.latestPose = [p.j1, p.j2, p.j3, p.j4, p.j5, p.j6];
      if (p.tcp) this.latestTcp = { x: p.tcp.x, y: p.tcp.y, z: p.tcp.z };
    },
    setLocalDemo(v) {
      this.localDemo = !!v;
    },
    setSimQ(q) {
      this.simQ = q.slice();
    },
    setSelIdx(i) {
      this.selIdx = i;
    },
    setActiveView(v) {
      this.activeView = v;
    },
    /** 建立 WebSocket 连接，姿态推送到 store。 */
    connect() {
      connectWs({
        onStatus: (ok) => this.setStatus(ok),
        onPose: (p) => this.applyPose(p),
      });
    },
  },
});
