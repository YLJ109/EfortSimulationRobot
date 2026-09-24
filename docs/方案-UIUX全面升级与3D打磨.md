# 方案：UI/UX 全面升级 + 3D 画面打磨 + 摄像头实时链路加固

> 生成时间：2026-09-24 ｜ 状态：**待确认**（确认后分阶段实施）
> 依据：3 个只读审查代理（UI 布局主题 / Three.js 3D / 摄像头实时链路）+ 人工核实
> 铁律：纯前端/纯服务改动，**不动机器人运动链路**；所有改动在模拟器/浏览器验证，真机行为不变。

---

## 1. 现状与根因总览

### 1.1 黑屏闪屏 / 卡顿 / 摄像头捕捉不到的根因链

```
浏览器多个隐藏标签页持续跑 (轮询 + 3D 渲染循环)      ← App.vue loop() 不随隐藏停止
        ↓
GPU 显存 + 浏览器资源被多标签页挤爆
        ↓
D3D11 0x887A0005/0x887A0020（设备移除）→ WebGL context lost → 黑屏
ERR_INSUFFICIENT_RESOURCES → 前端资源耗尽
        ↓
后端/相机被高频轮询拖垮 → /stream 断流 → "摄像头捕捉不到"
```

**三条主线**：
- **渲染循环不停**：`App.vue:270` 的 `loop()` 在 `visibilitychange` 只停轮询、不停 RAF；隐藏页仍全速渲染消耗 GPU。
- **资源不释放**：manager 无统一 dispose；切官方模型只 `scene.remove` 旧零件、不释放几何/材质。
- **前端状态失真**：`camera.js` 只在 `refresh()` 成功时写 `status`，服务断连时旧状态残留，MJPEG 仍按旧 `opened` 连流 → 黑屏假"已开启"。

### 1.2 UI 主题不统一

全局设计令牌（`--bg/--panel/--accent` 等 10 个）已存在且可用，但有 **8 处硬编码颜色绕过变量**（`#ff2020`、`#ffd54a`、`#ff9a9a` 等散落在 index.html 与 5 个组件），`.axis` 两套列宽定义、SimObjectsPanel 红绿蓝未走色板。

### 1.3 布局与卡片

- 右侧栏内容超长时卡片被压扁（`.side > * { flex:none }` 语义失效），预设点位列表无 `max-height`。
- 折叠卡片折叠后仅隐藏内容（SimObjectsPanel A/B），用户不知道还有可展开项。
- 摄像头面板（z25）与关节 HUD（z10）可能重叠；窄屏 1120px 以下侧栏转纵向堆叠、折叠机制失效。

---

## 2. 阶段一：3D 稳定性（黑屏闪屏根治）

| # | 位置 | 改动 |
|---|---|---|
| G1 | `App.vue:270` | `loop()` 加 `document.hidden` 检查：隐藏即 `cancelAnimationFrame`，可见即重启（与轮询闸同步） |
| G2 | `App.vue:347` | `onBeforeUnmount` 调用 `manager.disposeAll()` |
| G3 | `three/manager.js` | 新增 `disposeAll()`：依次释放 cell/lab/safety/sim/ghost/robotModel + renderer 的 `forceContextLoss` + `dispose` |
| G4 | `three/manager.js:728` | 切换官方模型时遍历旧 `parts` 释放 geometry/material/texture |
| G5 | `three/manager.js:210` | 监听 `webglcontextrestored`：重建场景资源并恢复渲染（不再只能刷新） |
| G6 | `manager.js:454` | `setSize` 已节流；补 `setPixelRatio` 只在变化时调用 |

## 3. 阶段二：摄像头实时链路加固

| # | 位置 | 改动 |
|---|---|---|
| C1 | `camera.js` | `refresh()` 失败时置 `opened=false + serviceDown=true`，清空 status（不再残留旧"已开启"） |
| C2 | `CameraPanel.vue:173` | `connectStream` 前检查 `!cam.serviceDown && !document.hidden`；断连后清空 img src |
| C3 | `CameraPanel.vue` | `/stream` 加 `AbortController` 超时保护，断流自动重设 src（带退避） |
| C4 | `camera.js` | `camPost` 加 `AbortController` 8s 超时；`refresh` 轮询失败连续 3 次即停并置 serviceDown |
| C5 | `CameraPanel.vue` | 新增"服务不可用"明确 UI 态（红底横幅 + 重试按钮），区分"无画面"与"服务挂了" |
| C6 | 相机服务 | `_open_camera` 失败路径补齐 `self.cam=None` + `self.error` 复位（避免状态残留） |

## 4. 阶段三：主题统一

| # | 位置 | 改动 |
|---|---|---|
| T1 | `index.html` | 补设计令牌：`--panel3`、`--accent-soft`、`--ok-soft`、`--danger-soft`、`--radius`、`--shadow`、字体栈 |
| T2 | 8 处硬编码 | 统一改走变量（`#ff2020`→`var(--err)`、`#ffd54a`→`var(--warn)` 等） |
| T3 | `index.html:159/376` | 合并 `.axis` 为单一列宽定义 |
| T4 | `index.html` | 卡片统一样式：圆角/阴影/边框/标题条/折叠箭头，抽公共类 `.card` |
| T5 | 全局 | 字体、间距、按钮、输入框、滚动条样式统一 |

## 5. 阶段四：布局与卡片

| # | 位置 | 改动 |
|---|---|---|
| L1 | `index.html:52` | 侧栏改为 `flex-direction:column` + 各卡片 `flex:none` + 容器滚动，卡片不再被压扁 |
| L2 | `PointExecView.vue:282` | 预设点位列表加 `max-height + overflow:auto` |
| L3 | 折叠卡片 | 折叠后保留标题条（可再展开），SimObjectsPanel A/B 折叠后显示标题+状态点 |
| L4 | 窄屏 | 1120px 以下改为可横向滚动的固定宽度侧栏，保留折叠能力 |
| L5 | `CameraPanel.vue` | 与关节 HUD 的 z-index 错开（cam-panel z 提到 30） |
| L6 | 各视图 | 检查折叠卡片的折叠状态持久化到 localStorage |

## 6. 阶段五：3D 画面打磨

| # | 位置 | 改动 |
|---|---|---|
| D1 | `manager.js` | `renderer.toneMapping = ACESFilmicToneMapping` + `toneMappingExposure` |
| D2 | `manager.js` | 开 `shadowMap`（机器人/站台/围栏投地面软阴影） |
| D3 | `manager.js:228` | OrbitControls 启用阻尼（已有）、加 `minPolarAngle` 限制俯仰 |
| D4 | `cell.js` | 地坪材质精调：金属/粗糙度/清漆统一，加网格衰减 |
| D5 | 官方数模 | 材质统一走 `MeshPhysicalMaterial` + 环境光，金属感更真实 |
| D6 | 背景/雾 | 场景背景/雾颜色与主题 `--bg` 对齐，房间灯光亮度精调 |

---

## 7. 实施与验收

| 阶段 | 范围 | 验收门 |
|---|---|---|
| 一 | 3D 稳定性 G1~G6 | 隐藏标签页 30s 后 GPU 占用归零；切模型 5 次内存不涨；pytest+verify 全绿 |
| 二 | 摄像头 C1~C6 | 拔网线模拟：前端 30s 内显示"服务不可用"；恢复后自动重连出画面 |
| 三 | 主题 T1~T5 | 全站无硬编码色（grep 验收）；截图对比 |
| 四 | 布局 L1~L6 | 侧栏折叠不再压扁卡片；窄屏可用；卡片折叠状态刷新保持 |
| 五 | 3D 打磨 D1~D6 | 视觉截图对比；帧率不降（≥30fps） |

每阶段独立 commit，问题整阶段 revert。**全程零真机风险声明**：纯前端样式/资源管理改动 + 相机服务状态逻辑；机器人运动链路、速度锁定 5%、J1-J5 锁定均不动。

---

## 8. 待确认

1. 五个阶段是否全部执行？
2. 阶段一 G5（context 自动恢复）与 G3（disposeAll）工作量大，是否优先？
3. 是否有品牌主色/深色浅色偏好（当前深色主题，主色橙色 `#ff7a18`）？
