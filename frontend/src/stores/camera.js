// =====================================================================
// Pinia store：视觉检测（海康相机 + YOLO）。迁移自原 main.js。
// 纯状态 + 请求动作；MJPEG 流的 <img> 管理由 VisionView 组件负责。
// =====================================================================
import { defineStore } from "pinia";
import { cameraBase } from "../config.js";

const camBase = cameraBase;

export const useCameraStore = defineStore("camera", {
  state: () => ({
    status: null,   // 相机服务 /status 快照
    error: "",
  }),
  getters: {
    busy: (s) => !!(s.status && (s.status.opening || s.status.closing)),
    opened: (s) => !!(s.status && s.status.opened && !s.status.closing),
  },
  actions: {
    async camPost(path, body = {}) {
      const r = await fetch(camBase() + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return r.json();
    },
    /** 拉取相机状态，返回是否处于切换过渡中（用于加速轮询）。 */
    async refresh() {
      try {
        const r = await fetch(camBase() + "/status");
        const s = await r.json();
        this.status = s;
        this.error = "";
        return !!(s.opening || s.closing);
      } catch (e) {
        this.error = "相机服务未启动（需运行 camera/camera_service.py，或 run.bat camera）";
        return false;
      }
    },
    async open() {
      const r = await this.camPost("/open");
      this.status = r.status;
      await this.refresh();
    },
    async close() {
      const r = await this.camPost("/close");
      this.status = r.status;
    },
    async setAI(enabled) {
      await this.camPost("/config", { enabled });
      await this.refresh();
    },
    async setModel(model) {
      await this.camPost("/config", { model });
      await this.refresh();
    },
    async setConf(conf) {
      await this.camPost("/config", { conf });
    },
    async setImgsz(imgsz) {
      await this.camPost("/config", { imgsz });
    },
    async snap() {
      try {
        return await this.camPost("/snapshot-save");
      } catch (e) {
        return { ok: false, error: "相机服务不可用" };
      }
    },
    /** 手动重连相机（网线在摄像头/机器人间切换后，点此触发，无需重启服务）。 */
    async reconnect() {
      try {
        await this.camPost("/reconnect");
        await this.refresh();
        return true;
      } catch (e) {
        this.error = "重连失败: 相机服务不可用";
        return false;
      }
    },
    /** 卸载 YOLO 检测模型，释放 torch 常驻内存（可达数 GB）。 */
    async unload() {
      try {
        const r = await this.camPost("/unload");
        await this.refresh();
        return r;
      } catch (e) {
        this.error = "卸载失败: 相机服务不可用";
        return { ok: false };
      }
    },
  },
});
