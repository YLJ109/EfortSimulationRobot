// WebSocket 客户端: 断线指数退避重连。
// onPose：姿态帧（type==="pose"）；onRcStatus：控制器寄存器快照帧（type==="rc_status"）；
// onStatus：连接通断回调。rc-status 经此通道后，前端不再单独轮询 /api/rc-status。
import { wsUrl } from "../config.js";

export function connectWs({ onPose, onStatus, onRcStatus }) {
  let ws = null;
  let retry = 0;
  let closed = false;
  let timer = null;

  function open() {
    // ★ 防止重复连接：先关闭旧的 WebSocket 和定时器
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (ws) {
      try { ws.close(); } catch (e) {}
      ws = null;
    }

    ws = new WebSocket(wsUrl());
    ws.onopen = () => {
      retry = 0;
      onStatus && onStatus(true);
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (!msg || !msg.type) return;
        if (msg.type === "pose") { onPose && onPose(msg); }
        else if (msg.type === "rc_status") { onRcStatus && onRcStatus(msg); }
      } catch (e) {
        /* 忽略坏帧 */
      }
    };
    ws.onclose = () => {
      onStatus && onStatus(false);
      ws = null;  // ★ 清理引用
      scheduleReconnect();
    };
    ws.onerror = () => {
      try { ws.close(); } catch (e) {}
    };
  }

  function scheduleReconnect() {
    if (closed) return;
    // ★ 防止重复调度：先清除旧定时器
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    retry = Math.min(retry + 1, 6);
    const delay = Math.min(500 * 2 ** retry, 8000);
    timer = setTimeout(open, delay);
  }

  open();

  return {
    close() {
      closed = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (ws) {
        try { ws.close(); } catch (e) {}
        ws = null;
      }
    },
  };
}
