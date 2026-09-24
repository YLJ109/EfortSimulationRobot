# 方案：将 rc-status 并入 WebSocket 推流（省一路轮询）

> 对应交接文档 §16 待办第 1 项。
> 目标：前端「真机链路就绪卡」去掉对 `/api/rc-status` 的 4 秒轮询，
> 改为消费后端经 `/ws/pose` 推来的 `rc_status` 数据帧。
> 生成时间：2026-09-23

## 1. 现状

- 前端 `stores/rcReady.js` 每 4s `fetch(apiUrl("/rc-status"))` 一次。后端
  `robot.py::rc_status` 内部调 `motion.modbus.rc_snapshot()`（**3 个 Modbus 事务**）。
- 姿态 / 事件已经走 WS：后端 `collector._loop` 每循环 `hub.broadcast(pose帧)`，
  `events.py::emit` 也 `hub.broadcast(event帧)`；前端 `App.vue onMounted → robot.connect()`
  建立**常驻** `/ws/pose` 连接（`net/ws.js`），`onmessage` 目前只分发 `type==="pose"`。
- 控制器寄存器快照（模式/伺服/报警/程序/触发位）是现场排障的高频数据，
  没必要单独占一路 HTTP。

## 2. 方案

### 2.1 后端（collector + ws）

- 在 `collector._loop` 中，按 `RC_STATUS_INTERVAL = 4.0` 秒低频做一次
  `self.modbus.rc_snapshot()`（**复用采集 socket**，纯 FC3 只读，与读关节角
  同一线程串行、共享 `io_lock`，天然不冲突；写路径在 `motion.modbus`，互不干扰）。
  - 真实模式：组装与 `/api/rc-status` 完全一致的字段的帧：
    `{ "type":"rc_status", ok, bits, mode, speed_pct, alarm1/2, prog, joints,
       jog_trig, jog_done, real_enabled, service_program, ready, t }`，
    再 `hub.broadcast`，并缓存到 `self.latest_rc_status`。
  - 模拟 / 离线：**不做真实读**（避免 TCP 超时拖住采集循环），直接广播
    `ok:false + error + real_enabled`，同样缓存。
- `ws.py` 握手时，除当前 pose 帧外，再推一帧 `collector.latest_rc_status`
  （有值才推），保证新连接立即能看到就绪态，不必等下一个广播周期。

### 2.2 前端

- `net/ws.js::connectWs` 增加 `onRcStatus` 回调，`onmessage` 对
  `type==="rc_status"` 分发。
- `stores/robot.js::connect()` 用 `onRcStatus` 把帧转给 rcReady store：
  `useRcReadyStore().ingest(payload)`；在回调内惰性取 store，避免顶部循环依赖。
- `stores/rcReady.js`：
  - **去掉 4s `setInterval`**，改为 WS 帧驱动：新增 `ingest(payload)` action
    更新 `snap/error/at`。
  - **保留** `load()`（GET `/api/rc-status`）作**种子**与「一键就绪 / 声明档位」
    完成后的**即时刷新**（POST 动作后立刻拿一次快照，不等下一帧，更跟手）。
  - `start()` 只拉一次种子（不再起定时器）；`stop()` 保留空壳兼容组件卸载。
  - 常量 `RC_POLL_MS` 语义改为 **WS 数据新鲜度窗口**，新增 `stale` getter
    （一段时间没收到 WS 帧 = 认为过期，UI 不再显示旧绿）。

## 3. 保持不变 / 兼容

- `/api/rc-status` 端点**保留**（作种子 + 兼容其它调用者）。
- `/api/ready`、`/api/control/run-mode` 不变。
- 后端测试全部与 WS/collector 广播解耦，无需改动。
- 安全：测试进程的双闸强制关（`EFORT_REAL_MOTION=0`）→ 模拟分支广播
  `ok:false`，**绝不写控制器**。

## 4. 守卫

- 更新 `frontend/tools/verify_rc_ready.mjs`：把「4s 轮询定时器」断言改为
  **WS 注入通道**断言（`net/ws.js` 分发 rc_status、robot store 接线、
  rcReady store 存在 `ingest(`）；保留 load/seed、后端契约等既有断言不变。

## 5. 验收

- 后端 `pytest` 全绿；前端 `verify`（含更新后的 verify_rc_ready）全绿；
  vite build 成功。