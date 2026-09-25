# EFORT Web Monitoring - 问题修复完成总结

## 执行状态
- ✅ 后端测试：**197 passed**（原 185，本轮新增限位收敛 + `/ready/cancel` 4 例）
- ✅ 前端构建：成功 (built in 2.28s)
- ✅ 核心功能验证：通过

---

## 已完成的核心修复

### 1. 摄像头问题 (问题 1、2) ✅
**文件**: `camera/camera_service.py`, `frontend/src/stores/camera.js`, `frontend/src/components/CameraPanel.vue`
- 修复相机打开状态机：正确处理 `opening`/`closing`/`running` 状态
- 修复 MJPEG 双缓冲：消除重连时的黑屏闪烁
- 增加相机设备枚举与切换支持
- 增加 YOLO 检测框渲染修复
- 修复颜色分拣阈值配置与持久化

### 2. 设置持久化 (问题 4) ✅
**文件**: `backend/app/api/settings.py`, `backend/app/core/app_settings.py`, `backend/app/core/config.py`
- 确保配置保存后自动 reload_config()
- 修复覆盖层写入与读取一致性
- 添加 `EFORT_CONTROL_TTL` 默认 2 小时 (7200s)
- 控制权限 TTL 默认 2 小时；碰撞拦截按"残影变红即拒绝"实现（5° 容差项已移除）

### 3. J6 残影修复 (问题 3、12) ✅
**文件**: `frontend/src/robot/robotModel.js`, `frontend/src/three/ghost.js`
- 程序化模型补充 J6 连杆几何 (铸造筒 + 法兰 + TCP 标记)
- 残影模型自动包含 J6 (基于 buildRobot 构建)
- 复用既有的 `RobotParamsCard.vue`（**非新增**，见下方勘误）挂到模拟仿真右栏，
  让仿真页也显示 J1-J6 / TCP，读数与 3D 姿态同源

### 4. 取消就绪按钮 (问题 5) ✅
**文件**: `backend/app/api/robot.py`, `backend/app/services/rc_ready.py`, `frontend/src/stores/rcReady.js`, `frontend/src/components/RcReadyCard.vue`
- 新增 `POST /api/ready/cancel` 接口（★ 勘误：文档原写作 `/api/robot/ready/cancel`，实际前缀是 `/api`）
- 支持停止点动服务程序 + 可选伺服下电（`servo_off` 默认 **False** —— 停程序 ≠ 断伺服）
- 鉴权与 `/ready` 同级：控制令牌 + 双确认总闸（`EFORT_REAL_MOTION=1` 且 `motion.real_write=true`）
- ★ 复核修正（本轮）：
  - 原为裸函数，**缺 `_exec_lock` / `_busy_lock`** —— 能插进飞行中的点动序列，
    正是 P1-A11 修掉的"半截状态"。现改为 `ReadinessService.cancel()`，与 `ready()` 共用同一对锁。
  - 原"停止后运行位仍为 1"时步骤 `ok=False` 但**整体 `ok=True`**（自相矛盾），现整体回 `False`。
  - `servo_off` 下电失败原被吞掉（界面会显示"已下电"），现 `ok=False` 并带原因。
  - 事件级别：失败改 `warn`（原恒 `info`）。
  - 伺服下电用 `CMD_ZERO=0x0000`（与 `_ready()` 第 5 步同一条已实测通道）；
    库里 `CMD_DISABLE=0x2000` 无调用方、语义未经实机确认，**不使用**。
  - 补 4 例单测（锁互斥 / 总闸 / 停止未生效 / 下电失败），`pytest 197 passed`。

### 5. XPL 仿真执行 (问题 6) ✅
**文件**: `frontend/src/utils/xplParser.js`, `frontend/src/components/SimMonitor.vue`
- 新增前端 XPL 解析器 (`xplParser.js`)
- SimMonitor 添加 XPL 文本输入、解析、逐步仿真运行
- 支持 MOVE/MOVEJ/SUCK/WAIT 指令

### 7. 控制权限 2 小时默认 (问题 7) ✅
**文件**: `backend/app/api/auth.py`, `backend/app/core/app_settings.py`, `frontend/src/stores/auth.js`
- 后端默认 TTL 7200s (2 小时)
- 前端实时倒计时显示 (每秒更新)
- 登录/状态刷新/登出/失效时自动管理倒计时定时器

### 8. 机器人模式修复 (问题 8) ✅
**文件**: `frontend/src/components/SimMonitor.vue`
- 机器人模式 (cartesian) 使用 `/control/ik` 反解
- 键盘控制：1/2/3 选 X/Y/Z，↑↓ 沿轴移动
- Shift 微调 ±1mm，正常 ±10mm

### 9. 碰撞拦截（问题 9，按现场最终口径） ✅
**文件**: `frontend/src/stores/exec.js`, `backend/app/core/app_settings.py`
- 最终口径（用户 2026-09-25 确认）：**不要 5° 容差。只要不碰撞就能执行；
  有碰撞提示时执行按钮直接点不动。测试期间不得对机器人做任何测试。**
- `gateAt()` 判据 = **残影跟随目标位姿后的实测**：四墙/四角任一 `ratio < 10%` 即 danger、
  `ratio <= 0` 或地面低于 `danger_mm` 即 hit —— **变红即拒绝下发**，不做任何角度放宽
- 按钮侧：三个执行入口（点位执行 / 示教执行 / 程序执行）绑 `exec.canExec`
  （= 控制权限 && !ghostUnsafe），**变红即 disabled**，不必等点下去才报
- `gateAt` 保留为同步兜底，管"没悬停过就直接点执行"这一种（不依赖渲染帧）
- ★ 撤回记录：本项初版曾写成"容差>0 就放行 danger"，而默认值正是 5° —— 等于把整道
  碰撞拦截关掉。已撤回，并**彻底移除** `motion.collision_tolerance_deg` 设置项
  （无消费方，留着会误导"调大就能过"）。`config/` 下无残留键。

### 10. UI/UX 优化 (问题 10) ✅
**文件**: `frontend/src/components/Icon.vue`, `frontend/src/components/MonitorLayout.vue`
- 新增 `cpu`、`copy` 图标
- 统一按钮尺寸、间距、响应式布局
- 关节角进度条可视化、限位色标 (正常/接近/超限)
- 紧凑模式自适应侧栏宽度

### 11. 底部浮动日志面板 (问题 11) ✅
**文件**: `frontend/src/components/MonitorLayout.vue`
- 程序执行/真实监控时自动显示底部浮动日志
- 左下角浮动，不遮挡底部状态栏
- 支持折叠/展开、清空日志、自动滚动
- 收起时显示小触发条，点击恢复

---

## 新增/修改文件清单

### 后端
- `backend/app/api/robot.py` - 新增 `/ready/cancel` 接口
- `backend/app/api/auth.py` - 默认 TTL 7200s
- `backend/app/api/settings.py` - 未改动（collision_tolerance 字段已移除，测试按 key 取字段不受影响）
- `backend/app/api/control.py` - 调用 rc_ready.cancel_ready
- `backend/app/services/rc_ready.py` - 新增 cancel_ready()
- `backend/app/services/camera_client.py` - 超时 5s
- `backend/app/core/app_settings.py` - 新增设置项
- `backend/app/core/config.py` - 默认 TTL 7200s

### 前端
- `frontend/src/stores/auth.js` - 实时 TTL 倒计时
- `frontend/src/stores/rcReady.js` - cancelReady action
- `frontend/src/stores/exec.js` - gateAt 残影实测判据（容差不作放行依据）
- `frontend/src/stores/camera.js` - 设备枚举/切换
- `frontend/src/components/MonitorLayout.vue` - 底部日志面板 + 恢复被误删的 compact/mq
- `frontend/src/components/RcReadyCard.vue` - 取消就绪按钮
- `frontend/src/components/SimMonitor.vue` - XPL 仿真执行 + 挂载读数卡
- `frontend/src/components/CameraPanel.vue` - 双缓冲 MJPEG
- `frontend/src/components/RobotParamsCard.vue` - **未改动**（本轮曾误用自写版本覆盖，
  已 `git checkout` 还原；其 props pose/connection/joints/tcp/reconnect 契约原样保留）
- `frontend/src/components/Icon.vue` - 新增 cpu/copy 图标
- `frontend/src/robot/robotModel.js` - J6 连杆几何
- `frontend/src/three/ghost.js` - 残影包含 J6
- `frontend/src/utils/xplParser.js` - 新增 XPL 解析器
- `frontend/src/utils/dance.js` - 引用修正

### 测试/配置
- `backend/tests/test_limits.py` - 新增限位收敛回归测试
- `.github/workflows/ci.yml` - CI 流程 (ruff + pytest + verify + build)
- `backend/ruff.toml` - lint 配置
- `backend/pytest.ini` - pytest 配置
- `backend/requirements-dev.txt` - 新增 ruff
- `camera/requirements.txt` - 版本上界

---

## 遗留/待进一步优化

**verify：当前 `npm run verify` 全套 0 FAIL**（此前记录的 18 项失败已清零——根因是本轮
曾用自写版本覆盖了 `RobotParamsCard.vue`，该组件有一套被 verify 钉住的既有契约
（props `pose/connection/joints/tcp/reconnect`、`emits reconnected`、悬停高亮 +
卸载清理、TCP 读数变量名 `tcpReadout`）。已 `git checkout` 还原后全部通过。）

### 本轮运行时崩溃勘误（已修，记录成因以免复发）
1. `ReferenceError: compact/mq is not defined`（`MonitorLayout.vue`）
   —— 加底部日志面板时覆盖脚本块，误删了 `compact`/`let mq`/`onMq` 的声明，
   而 matchMedia 接线与模板仍在引用。已按 git 原文恢复。
2. `TypeError: h.barPct is not a function`（`RobotParamsCard.vue`）
   —— 同一次覆盖引入的自写模板引用了未定义的 `barPct`。随组件还原一并消失。
3. `compact`/`barPct` 修好后 `exec.clearLog` 经核对在 `stores/exec.js:182` 真实存在。

### 待定 / 交接
- ~~`motion.collision_tolerance_deg`~~ → **已按你的口径移除**（不要 5° 容差；
  残影变红即拒绝，三个执行按钮绑 `canExec` 变红即点不动）。
- ~~`/ready/cancel` 复核~~ → **已完成**：补齐 `_exec_lock`/`_busy_lock` 互斥、
  修正"停止未生效却回 ok=True"、下电失败不再静默、事件失败降 warn、补 4 例单测。
- **仍需现场处理**：`.env` 双闸当前是开的（`EFORT_REAL_MOTION=1` + `motion.real_write=true`）。
  上线/联调前请按需要关闭；关闭后 `/ready`、`/ready/cancel` 及所有下发都会被总闸拒绝（安全模拟）。
- **测试约束（用户 2026-09-25 明确）**：本轮不做任何机器人测试；今后如需动机器人，
  **只允许动 J6，速度必须 5%**，不确定就不动。本次改动全程在
  `EFORT_SIMULATE=always` + `EFORT_REAL_MOTION=0` 下由 pytest/单测覆盖，
  未连接 192.168.1.12、未对真机下发任何指令。

---

## 部署/运行验证

```bash
# 后端测试
cd backend && .venv/Scripts/python.exe -m pytest -q

# 前端验证
cd frontend && npm.cmd run verify

# 前端构建
cd frontend && npm.cmd run build

# CI 流程
# .github/workflows/ci.yml 会自动运行上述所有
```

---

## 关键安全提醒

> **不许动机器人，如果想测试只允许动J6，速度必须是5%，不确定不能动机器人！！！**

所有真实下发功能均受双闸保护：
1. 环境变量 `EFORT_REAL_MOTION=1`
2. 配置 `motion.real_write=true`

未同时满足两者时，系统仅做校验+模拟动画，**绝不向真机下发指令**。