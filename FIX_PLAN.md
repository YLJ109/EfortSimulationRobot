# EFORT Web Monitoring - 问题修复计划与执行记录

## 问题清单（用户反馈的 13 项）

| # | 问题 | 优先级 | 状态 | 备注 |
|---|------|--------|------|------|
| 1 | 摄像头无法开启，第一次可以开，偶尔打不开 | P0 | ✅ 已修复 | 相机服务状态机与前端轮询竞态 |
| 2 | 摄像头无法检测矩形物体，没有画框，也没有显示 | P0 | ✅ 已修复 | YOLO 检测结果未正确传递到前端 |
| 3 | 点位执行残影跟随 J6 无旋转，残影没有 J6 物体 | P0 | ✅ 已修复 | 程序化模型构建缺少 J6 连杆 |
| 4 | 系统设置没有保存好，每次打开重置掉 | P0 | ✅ 已修复 | 覆盖层写入后未 reload 配置对象 |
| 5 | 点位执行真机链路添加取消就绪按钮 | P1 | ✅ 已修复 | 新增 /ready/cancel 接口 |
| 6 | 模拟仿真添加运行 XPL 文件功能 | P1 | ✅ 已修复 | XPL 解析 + 仿真执行引擎 |
| 7 | 管理员控制时间默认 2 小时，实时同步时间 | P1 | ✅ 已修复 | 默认 TTL 7200s + 前端倒计时 |
| 8 | 模拟仿真机器人模式=关节模式，应为整体运动 | P1 | ✅ 已修复 | 直角坐标模式经 IK 反解 |
| 9 | 点位执行 5 度内预测不碰撞可运行，添加可调设置 | P1 | ✅ 已按最终口径完成 | **不要 5° 容差**；残影跟随变红即拒绝，三个执行按钮绑 `canExec` 变红即点不动 |
| 10 | 按钮过宽、UI-UX 问题 | P2 | ✅ 已修复 | 统一按钮尺寸、间距、响应式 |
| 11 | 程序执行运行日志移至左下角 | P2 | ✅ 已修复 | 底部浮动面板，不遮挡状态栏 |
| 12 | 模拟仿真/真机显示 J1-J6 参数并同步姿态 | P2 | ✅ 已修复 | 关节角面板实时同步遥测 |
| 13 | 搜索埃夫特机器人 PC 编程/本地代码运行方法 | P2 | 📋 文档 | 已整理到 DEV_GUIDE.md |

---

## 核心修改文件

### 后端
- `backend/app/services/camera_client.py` - 相机客户端超时与错误处理
- `backend/app/api/settings.py` - 设置保存后自动 reload
- `backend/app/api/robot.py` - 新增 /ready/cancel、/control/run-xpl
- `backend/app/services/rc_ready.py` - 取消就绪逻辑
- `backend/app/services/motion.py` - 碰撞容差设置
- `backend/app/core/app_settings.py` - control_ttl 默认值（collision_tolerance 项已按现场口径移除）
- `backend/app/core/config.py` - 配置 schema 扩展

### 前端
- `frontend/src/stores/camera.js` - 相机状态机修复、检测框渲染
- `frontend/src/components/CameraPanel.vue` - MJPEG 双缓冲、检测框叠加
- `frontend/src/stores/rcReady.js` - 取消就绪 action
- `frontend/src/components/RcReadyCard.vue` - 取消就绪按钮
- `frontend/src/components/SimMonitor.vue` - 机器人模式(直角坐标)修复
- `frontend/src/stores/exec.js` - XPL 解析、仿真执行、gateAt 碰撞拦截
- `frontend/src/components/PointExecView.vue` - 取消就绪按钮
- `frontend/src/components/ProgramExecView.vue` - 日志移至底部浮动面板
- `frontend/src/components/RobotParamsCard.vue` - J1-J6 实时同步面板
- `frontend/src/three/robotModel.js` - 补充 J6 连杆几何
- `frontend/src/three/ghost.js` - 残影模型包含 J6
- `frontend/src/stores/auth.js` - control_ttl 默认 7200s
- `frontend/src/components/MonitorLayout.vue` - 底部日志面板布局

---

## 执行步骤

### 步骤 1：相机服务修复（问题 1、2）
### 步骤 2：设置持久化修复（问题 4）
### 步骤 3：J6 残影修复（问题 3、12）
### 步骤 4：取消就绪按钮（问题 5）
### 步骤 6：XPL 仿真执行（问题 6）
### 步骤 7：控制 TTL 默认 2 小时（问题 7）
### 步骤 8：模拟仿真机器人模式修复（问题 8）
### 步骤 9：碰撞容差设置（问题 9）
### 步骤 10：UI/UX 按钮优化（问题 10）
### 步骤 11：日志面板底部浮动（问题 11）
### 步骤 12：关节角实时面板（问题 12）

---

## 验收标准

**已由脚本验证（可复跑）**
- [x] pytest **197 passed**（原计划 185；含本轮 `/ready/cancel` 新增 4 例 + 限位收敛用例）
- [x] `ruff check .` → All checks passed
- [x] `npm run verify` → 全套 **0 FAIL**（原计划 112 passed；现 9 个套件全部通过）
- [x] `npm run build` 成功且 **0 warning / 0 error**
- [x] 残影跟随目标位姿实测：四墙/四角 ratio<10% 或触地即变红 → **拒绝下发**
- [x] 三个执行入口（点位执行 / 示教 / 程序执行）按钮绑 `exec.canExec`（控制权限 && !ghostUnsafe），变红即 disabled
- [x] `collision_tolerance_deg` 设置项已**移除**（现场口径：不做角度放宽；留着会误导"调大就能过"）
- [x] `/ready/cancel` 与 `/ready` 共用 `_busy_lock` + `motion._exec_lock`，补 4 例单测

**需在跑起来的界面上人工确认（本轮按约定未启动服务、未连真机，故不勾）**
- [ ] 相机首次/二次打开稳定，检测框正常显示（需 camera_service :8100 在跑）
- [ ] 设置修改后刷新页面保持
- [ ] 残影包含 J6 且随关节角旋转
- [ ] 真机链路卡"取消就绪"按钮：点击后程序停止、伺服可选下电（**需真机，且只许 J6 + 5%**）
- [ ] 模拟仿真可加载 .xpl 文件并逐步执行
- [ ] 管理员登录后控制权限默认 2 小时，顶栏实时倒计时
- [ ] 模拟仿真"机器人模式"下方向键驱动末端 XYZ 移动（经 IK）
- [ ] 所有按钮宽度自适应、间距统一、无溢出
- [ ] 程序执行日志在左下角浮动面板，不遮挡状态栏/提示
- [ ] 真实监控/模拟仿真均显示 J1-J6 实时角度，与遥测同步

> ⚠️ 约束（用户 2026-09-25）：**本轮不做任何机器人测试**；今后需要动机器人时
> **只允许动 J6、速度必须 5%**，不确定就不动。