// WebSocket 客户端: 断线指数退避重连。
import { wsUrl } from "../config.js";

export function connectWs({ onPose, onStatus }) {
  let ws = null;
  let retry = 0;
  let closed = false;
  let timer = null;

  function open() {
    ws = new WebSocket(wsUrl());
    ws.onopen = () => {
      retry = 0;
      onStatus && onStatus(true);
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg && msg.type === "pose") onPose && onPose(msg);
      } catch (e) {
        /* 忽略坏帧 */
      }
    };
    ws.onclose = () => {
      onStatus && onStatus(false);
      scheduleReconnect();
    };
    ws.onerror = () => {
      try { ws.close(); } catch (e) {}
    };
  }

  function scheduleReconnect() {
    if (closed) return;
    retry = Math.min(retry + 1, 6);
    const delay = Math.min(500 * 2 ** retry, 8000);
    timer = setTimeout(open, delay);
  }

  open();

  return {
    close() {
      closed = true;
      if (timer) clearTimeout(timer);
      if (ws) try { ws.close(); } catch (e) {}
    },
  };
}
