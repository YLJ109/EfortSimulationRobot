# FIT-RCMS 项目全面交接文档（AI 接管版）

> **文档用途**：让任何新对话 / 新 AI 一次性接管本项目，无需再向用户追问背景。
> **生成时间**：2026-09-23 深夜（Stage D 真机联调完成、Web J6 点动验证成功之后）。
> **项目位置**：`D:\EFORT_Projects\EFORT_Web_Monitoring`（**不在** WorkBuddy workspace 内）。
> **阅读顺序**：新接手先读 §1~§4（是什么/怎么跑），改代码前必读 §7（寄存器）+ §8（权限）+ §13（坑清单），历史背景见 §15。

---

## 1. 项目定位

**FIT 埃夫特智能机器人远程控制与监控系统**（英文：FIT EFORT Intelligent Robot Remote Control & Monitoring System，缩写 **FIT-RCMS**，版本 0.4.0）。

EFORT **ER8-700H** 六轴工业机器人（控制器 RP-2/Robox 系，序列号 OSN201BE16081）的全栈 Web 系统：

- **监控**：Modbus TCP 实时采集关节角 → WebSocket 推送 → Three.js 3D 数字孪生（程序化模型 + 官方 GLB 原件 + 残影 + 安全围栏 + 实验室房间）。
- **仿真**：机器人离线时自动降级模拟流；模拟模式下有状态仿真机 SimRobot（指令→按 45°/s 走位→到位停住）。
- **控制**：Web 点动 J1~J6（增量/连续）、滑块示教、点位库、程序执行、一键就绪、急停 —— **真机链路已实测打通**（J6 +1.000° 误差 0）。
- **视觉**：海康 MV-CU120-10GM 工业相机独立服务（:8100），MJPEG 预览 + YOLO 按需检测 + 颜色分拣规则联动。
- **运维**：事件总线审计、配置包导出导入、围栏版本回滚、系统设置中心、全局快捷键。

## 2. 技术栈与目录结构

```
D:\EFORT_Projects\EFORT_Web_Monitoring\
├── backend\          FastAPI + uvicorn + SQLAlchemy 2.0 + SQLite(WAL)，端口 8000
│   ├── .venv\        Python 3.13 venv（★ 用 .venv\Scripts\python.exe，别用系统 python）
│   ├── app\
│   │   ├── api\      薄路由：auth/control/robot/points/programs/recordings/events/
│   │   │             safety/settings/system/vision/frames/ws
│   │   ├── services\ 核心业务：modbus / motion / jog / jog_frames / rc_ready /
│   │   │             runmode / safety_guard / sim_robot / collector / hub /
│   │   │             events / kinematics / camera_client / vision_ingest / vision_rules
│   │   ├── core\     config / exceptions / deps / middleware / logger / brand / app_settings
│   │   └── db\       models / crud（SQLite: data\robot.db）
│   └── tests\        pytest 185 例（★ 2026-09-25 静态计数，随代码增减需同步）
├── frontend\         Vue 3(<script setup>) + Pinia + Vite + Three.js，★无 Router（tab 用 v-show 保活 3D）
│   ├── src\
│   │   ├── components\  18 个组件（详见 §10）
│   │   ├── stores\      robot/safety/recording/camera/auth/exec/settings/ui/link/rcReady/poseAuthority
│   │   ├── three\       manager / safety / lab / cell / ghost（3D 核心）
│   │   ├── net\control.js    apiControl() 自动附 X-Control-Token
│   │   ├── utils\       safetyLabels / jointScale（纯函数）
│   │   └── tools\ → frontend\tools\  18 个 headless 守卫 + 2 个测量工具
│   └── dist\          构建产物（后端按磁盘实时读它 → 前端改动刷新浏览器即生效）
├── camera\           相机独立服务 camera_service.py（:8100，海康 MSDK）
├── config\           robot.yaml / safety.json / app_settings.json / programs.json
├── data\             robot.db（SQLite WAL，~20MB）
├── docs\             12 份方案/报告文档（含《控制器Modbus寄存器勘察报告.md》实测确认版）
├── programs\         程序目录 JSON
├── scripts\          reg_survey.py（寄存器只读勘察）等
├── run.bat / setup.bat
└── .env              环境变量（口令/双闸，见 §4）
```

## 3. 网络拓扑与硬件

| 设备 | 地址 | 说明 |
|---|---|---|
| 机器人控制器（RP-2） | `192.168.1.12:502` Modbus TCP | unit_id=1，读 FC3 / 写 **FC6 逐寄存器** |
| 后端 API | `0.0.0.0:8000`（本机 `127.0.0.1:8000`） | 同时托管 frontend/dist 静态页 + `/ws/pose` |
| 相机服务 | `127.0.0.1:8100` | 海康 MV-CU120-10GM（`192.168.1.51`，GIGE） |
| 示教器 | 控制器面板 | 模式旋钮 T1/T2/AUTO/REMOTE，三段使能开关 |

- 关节角读取：FC3 **一次读 32 个寄存器（地址 0 起）最稳**；`qty=23` 确定性失败；单点探测 `qty=1 @addr10` 返回异常 0x02 但 `qty=12` 正常 —— **可读性必须按 (start,qty) 整块判断**。
- J1~J6 = reg 10..21，float32 **CDAB 字序交换**（`struct.unpack('>f', pack('>HH', high, low))`）。
- 采样：读 10Hz / 入库 2Hz / WS 推送 20Hz；历史保留 30 天；collector 降级阈值 FAIL_LIMIT=30（约 3 秒容错）。

## 4. 运行环境与启动（一个都不能漏）

### 4.1 环境变量（`.env`，改后必须重启后端）

| 变量 | 当前值 | 说明 |
|---|---|---|
| `EFORT_ADMIN_PASSWORD` | `admin123` | 管理员口令（Web「请求控制」输入它） |
| `EFORT_CONTROL_TTL` | `7200` | 控制令牌时长秒数；**代码默认 7200 = 2 小时**（现场口径，2026-09-29 用户确认）；设置页可改并自动回写本文件；**重启后端后令牌全部失效，必须重新获取**。★ 这是**启动默认值**：启动只读它，所以它写 `1800` 就会出现"设置页改成 2 小时、一重启又回到 30 分钟" |
| `EFORT_REAL_MOTION` | `1` | ★ 真机下发总闸 1/2（2026-09-23 用户授权真机联调）。恢复安全模拟改回 0 并重启 |
| `EFORT_SIMULATE` | （注释，默认 auto） | auto=不可达则模拟 / always / never |
| `EFORT_OPERATOR_PASSWORD` | （未启用） | 配置后启用 operator 角色 |
| `EFORT_DB_URL` | （注释） | 数据库覆盖 |

### 4.2 双闸（真机下发铁律）

**真实下发 = `EFORT_REAL_MOTION=1`（env）** 且 **`motion.real_write: true`（config/robot.yaml）**，缺一即模拟。当前**双闸已开**（用户授权）。统一真值表：`motion.py::real_motion_env_active()`（认 1/true/yes/on）+ `real_write_enabled()`。`tests/conftest.py` 模块级强制 `EFORT_REAL_MOTION=0` —— **测试进程绝不允许写真机**。

### 4.3 启动 / 停止 / 重启

```powershell
# 后端（PowerShell 工具 Start-Process 可靠；bash nohup 的进程会随命令结束被回收）
Start-Process -FilePath "D:\EFORT_Projects\EFORT_Web_Monitoring\backend\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000" `
  -WorkingDirectory "D:\EFORT_Projects\EFORT_Web_Monitoring\backend" -WindowStyle Hidden
# 探活：netstat -ano | findstr :8000 + curl http://127.0.0.1:8000/api/system/health
# 杀旧后端：tasklist 找 PID → taskkill //F //PID <pid>（注意 uvicorn Windows 父子双进程属正常）
# 相机服务：camera/camera_service.py，:8100
```

- 后端**按磁盘实时读 frontend/dist** → 前端改完构建后刷新浏览器即生效，**不必重启后端**；后端 Python 代码改动才需要重启。
- curl 要带 `--noproxy "*"`（沙箱代理会拦成 502 "upstream connect failed"）。

### 4.4 运行时

- 后端：`backend\.venv`（Python 3.13.14）。
- 前端构建用**托管 node**：`C:\Users\FIT\.workbuddy\binaries\node\versions\22.22.2-3\node.exe`；vite 必须直跑 `node node_modules/vite/bin/vite.js build`（经 npm 跑会崩溃 OOM）。

## 5. 机器人使用说明（现场要点）

- **程序 200 / JOGSVC**：控制器上的**常驻点动服务程序**（`WHILE true → 清完成位 → WAIT(mtcp_ro_b[0]) → MJOINT(目标) → 置完成位 → WAIT(!触发)`）。PC 点动的全部原理 = 往它喂目标角 + 拍触发位。控制器里另有程序 411（用户日常程序），addr5 可读当前程序号。
- **一键就绪 8 步**（`services/rc_ready.py`，Web 卡片按钮 / `POST /api/ready`）：双闸守卫 → 快照 → 触发位守卫（=1 拒绝）→ 手动档守卫（T1/T2 拒绝）→ 清报警(0x1009) → 伺服重吸合(0x0000→等0.6s→0x1001，轮询1.5s) → 加载程序(写103+0x1011，轮询Bit11 2.5s) → 运行(0x1013，轮询Bit6)。全程**不写目标角区、不产生运动**。
- **报警码**：`1812`=安全门（安全回路不满足，检查门/光栅）· `3909`=示教器未连接（bcc 通讯断开，远程加载/运行都依赖示教器在线，恢复连接后重试）· `5005`=加载的程序不存在（确认 JOGSVC 存在、示教器没停在文件管理器/未断开）· `4902`=XPL 文件损坏。
- **硬规则**：必须 AUTO/远程档；先加载成功才准运行；加载前别发停止(0x1005)；清报警后再加载。
- **点动实际速度由 40103 速度设定控制**（5%≈1.3°/s，100%≈60~90°/s），不是程序里的 `v100perc`；Web 点动会先写 102(=40103) 速度再执行。
- **三段使能开关**：中间档=伺服上电，重按到底=急停。三种奇异点（腕部 J5≈0 / 肩部 / 肘部）处置=切回关节坐标系。

## 6. 开源 / 参考实现（重要资产）

- **私有仓库 `YLJ109/efort_panel_pyqt6`**（PyQt6 参考面板，**同一台机器人上实机验证过**）。★ 网页 WebFetch 只会拿到 404；`gh` 已登录 `YLJ109`（含 repo scope）→ 用 `gh repo view YLJ109/efort_panel_pyqt6` / `gh api -H "Accept: application/vnd.github.raw" repos/YLJ109/efort_panel_pyqt6/contents/<path>` 逐文件拉取（`gh repo clone` 在沙箱被 SIGTERM 杀掉，别用）。
- **本地只读副本 `D:\EFORT_Projects\_ref_efort_panel\`**：`efort_client.py`（纯 socket+struct，**不需要 pymodbus**）、`app/worker.py::_do_ready`（就绪顺序）、`tools/ready_up.py`（一键就绪，本次新增，零运动）、`tools/jog_axis.py`（单轴点动+CSV 采样）。
- Stage D 的全部寄存器表/命令字/时序都来自它 + `docs/控制器Modbus寄存器勘察报告.md`（实测确认版）+ `scripts/reg_survey.py`（全 FC3 只读勘察：`windows|dump|joints|watch [秒]`）。

## 7. ★★★ Modbus 寄存器表（0 基址；手册写作 4xxxx = +1；实机确认）

### 读（FC3）
| 地址 | 含义 |
|---|---|
| `0` | 状态位：Bit0手动 / Bit1自动 / Bit2远程 / Bit3伺服 / Bit4报警 / Bit5急停 / Bit6程序运行 / Bit11程序加载 / Bit12伺服就绪 |
| `2` | 运行速度%（=40103） |
| `3,4` | 报警码 1/2（1812 安全门…） |
| `5` | 当前程序号 |
| `10..21` | J1~J6 float32 CDAB |
| `34` | 机器人→PC 位区，Bit0=**点动完成回执**（手册 40035） |

### 写（FC6 逐寄存器，回显在 body[3:5]）
| 地址 | 含义 |
|---|---|
| `100` | 指令字（=40101） |
| `102` | 速度设定%（=40103） |
| `103` | 目标程序号（=40104） |
| `134` | PC→机器人位区，Bit0=**点动触发**（手册 40135） |
| `138..143` | J1~J6 **目标绝对角 ×100 的 int16 补码**（手册 40139~40144） |

### 命令字（同沿单条写，恒保留 Bit0+Bit12）
`0x1001` 伺服上电 · `0x1011` 加载 · `0x1013` 运行 · `0x1005` 停止 · `0x1009` 清报警 · `0x2000` 取消使能 · `0x0000` 全清。重吸合序列：`0x0000` → **等 0.6s** → `0x1001`（吸合延迟实测 ≈0.55s）。

### 点动时序（`modbus.py::rc_jog_execute`，实测过）
触发位必须空闲 → 写目标角+**回读逐字节校验** → **settle 0.15s**（防"读到旧目标→零位移却秒回完成位"竞态）→ 置触发 → 轮询 40035.Bit0（超时按 45°/s 折算）→ finally 撤触发。**"只动目标轴"** = `tgt=list(cur)` 只改目标轴写回，天然防冲预置。全程共享 `self._io_lock`（采集线程与点动线程共用 socket）。

## 8. 控制权限体系（全面细节）

1. **登录**：`POST /api/auth/login {"password":"admin123"}` → 48 字符 token（PBKDF2-SHA256 10 万次+盐，存内存 → **后端重启即全部失效，必须重新获取**）。`GET /auth/status`、`POST /auth/logout`。
2. **角色**：admin（全能）/ operator（只能操控；`EFORT_OPERATOR_PASSWORD` 未配则禁用）。`require_control`(401) + `require_admin`(403)。
3. **TTL**：默认 0=不限时；设置页「控制权限时长」可改（`POST /api/settings/control-ttl`，admin，自动回写 .env）；**不追溯**，只影响新签发令牌。
4. **档位声明（runmode）**：模式旋钮是硬件，**Modbus 读不到** → Web 侧"声明"（`POST /api/control/run-mode`，`DELETE` 撤销）+ 控制器寄存器回读 `confirmed`。**语义已按实机反转**：`JOGGABLE = {T1:False, T2:False, AUTO:True, REMOTE:True}` —— 实测 T1/T2 下控制器忽略一切 PC 指令（写成功但不动）。声明不持久化、有 2h 有效期。
5. **围栏互锁**：前端每秒 `POST /api/safety/live` 上报余量；`/api/control/move` 前 `check()`，danger/hit→409；无上报→放行但记 `control.interlock_absent`（`motion.require_live_safety=true` 时拒绝）。
6. **接口权限**：读接口全公开；点位/程序增删改+配置包导出需控制令牌；围栏保存/重置/事件清空/版本回滚/备份导入/设置写入需 admin。
7. **写路径真值表**：`motion.real = EFORT_REAL_MOTION==1 且 motion.real_write`；`/api/system/health` 的 `robot.mode="real"` 只是 TCP 可达探测，**看"能不能碰真机"只看 motion.mode**。

## 9. 实测验证记录（2026-09-23 深夜）

- `/api/rc-status`：mode=auto、servo=1、alarm=0、estop=0、程序 200 loaded+run、触发位空闲、real_enabled=true、ready=true。
- 声明 AUTO → `confirmed:true`；`POST /api/control/jog/step {"joint":6,"dir":1,"angle_deg":1,"speed_dps":15}` → **J6 126.30°→127.30°（+1.0000° 误差 0）**；-1° 精确复位。`mode:"real"`。
- 更早（参考项目脚本）：J6 124.30→125.30→126.30，其他 5 轴位移 ≤0.001°，标准 S 型加减速，启动延迟 0.65s。
- ★ `/api/pose` 返回 `j1..j6` 平铺字段（**没有 joints 数组**）；`/api/rc-status` 才有 `joints` 数组。

## 10. 前端架构要点

- **姿态权威模型（Stage A）**：`stores/poseAuthority.js::resolvePose` 全站唯一优先级：override(仅前台 owner) > demo > command(simulated && !tracking && cmd) > telemetry。`applyRobotPose` 全 src 只允许 App.vue RAF 循环一处调用（verify 钉死）；`noteCommand(target,tcp)` 记录指令目标；`setLocalDemo` 已灭绝。
- **三页共用**：`RobotParamsCard`（真实监控/点位执行/程序执行，只读参数卡）、`ExecControlCard`（控制权限+速度+急停）、`RcReadyCard`（**真机链路就绪**：双闸/档位/伺服/报警/急停/程序/触发位七行 + 一键就绪 + 声明 AUTO/远程；`stores/rcReady.js` 4s 轮询 `/api/rc-status`）。
- **3D**：`three/manager.js` 单例 Renderer+官方 GLB 原件（clone 单 mesh 会放大 1000×）；官方数模抬升必须在所有 `attach()` 之后设 `root.position.y`；`baseLiftY()` 统一抬升与碰撞零点。`safety.js` 围栏引擎（取色必须走 `colorOf/opacityOf`，绝不 `colors[state]||green`）。`lab.js` 房间 LAB_SIZE=14（**用户要大房间，勿删**）。`ghost.js` 残影（opacity .26/depthWrite=false/不参与碰撞）。
- **页面**（8 tab，v-show 保活）：live / sim / rec(轨迹对比) / point / program / ops(EventsView) / settings / about。权限门控在 `tabs.js`（纯逻辑）。
- **快捷键**（shortcuts.js）：`1~8` 切模块 / `E` 侧栏 / `Q` 摄像头 / `空格` 运行停止(仅 program) / `Esc` 急停 / `Enter` 复位。
- **轮询唯一性**：`/system/guide` 只有 link store 一个轮询者（5s）；`/rc-status` 只有 rcReady store（4s）。
- `apiUrl(p)`/`apiControl(p)` **自动补 /api 前缀**，只能传去掉 /api 的路径。
- 品牌三处锁（verify_link 断言一致）：`frontend/index.html <title>` / `src/brand.js` / `backend/app/core/brand.py`；缩写 FIT-RCMS 刻意不含 AI（导出文件名前缀）。

## 11. 后端架构要点

- 分层 `core/ + api/(薄) + services/ + db/`；统一错误 `{"code","message","detail"}`（AppError 文案在 `message`，HTTPException 在 `detail`）。
- `services/events.py::emit(category,level,action,message,detail,actor,ip)`：落库 system_events + WS 广播 + 写日志，**永不抛异常**、线程安全。
- `services/collector.py`：Modbus 采集线程；自动恢复与手动 reconnect 都尊重 `simulate=always`。
- `services/sim_robot.py`（Stage C）：模拟模式有状态走位（45°/s×speed%，最短 0.4s），on_command 先推进在途走位防起点回跳。
- `services/motion.py::command()`：真实分支写速度→`rc_jog_execute`；模拟分支调 sim_robot；estop 用 `rc_estop(True)`(0x1005)。
- `services/jog.py`：连续点动引擎（watchdog 1500ms 死人开关、tick 100ms）；**estop 必须先 jog.stop**；加锁方法内禁止调用另一个加锁方法（曾死锁）。
- 数据库：`events`（旧，勿动）+ `system_events` + `safety_config_versions`。**给已有表加列必须新建表**（create_all 不补列）。
- `config/safety.json` 原子写+强校验；最多 8 区域（矩形/四点/圆形）；ground 默认 150/80/30mm。

## 12. 测试与守卫体系

```bash
# 后端（197 passed · ★ 2026-09-25；原 185 + 限位收敛 + /ready/cancel 4 例）
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q --basetemp=".pytest_tmp" -p no:cacheprovider
# （跑完删 .pytest_tmp；被杀的 pytest 会残留进程锁 db 文件）

# 前端（18 工具全绿，0 FAIL）
cd frontend && npm run verify
# 构建：node node_modules/vite/bin/vite.js build （勿经 npm）
```

前端工具及断言数：check_imports(53 文件) / check_cjk_bare(中文引号守卫) / verify_pose_authority(45) / verify_lab(44) / verify_cell(52) / verify_ground(67) / verify_safety(49) / verify_safety_text(82) / verify_official_model / verify_kinematics / verify_ghost(13) / verify_highlight(8) / verify_tabs(56) / verify_ui(41) / verify_robot_params(78) / verify_link(78) / **verify_rc_ready(34)** / verify_shortcuts(135) / verify_about(126)。

## 13. 关键坑清单（43 条，逐条都踩过，接管必读）

1. CORS：`allow_origins=["*"]` 不能与 `allow_credentials=True` 同用。
2. 根路径路由须在 `app.mount("/")` 前注册。
3. yaml 相对路径别直接连库，用 `db_path()` 或 `EFORT_DB_URL` 绝对路径。
4. `datetime.utcnow()` 已废弃 → `datetime.now(timezone.utc)`。
5. `.bat` 须纯 ASCII + CRLF + `chcp 65001`。
6. 前端构建用托管 node 直跑 vite.js，勿 npm 全局。
7. 后端进程轮次结束被回收 → PowerShell Start-Process 后台起。
8. 环境不能读 PNG（Read 过滤）→ 验证靠 curl + 产物 grep。
9. uvicorn Windows 父子双进程属正常。
10. collector 降级阈值 FAIL_LIMIT=30。
11. ★★ 同文件多处 Edit **不能并行**（同消息互相覆盖只留最后），串行改 + Grep 复核。
12. ★★ 改"按状态取色"必加 headless 断言钉死每个状态最终值。
13. ★ PowerShell 不回显 stdout → 重定向 `$env:TEMP\*.txt` 再 Read；删文件用 `[System.IO.File]::Delete()`（Remove-Item 静默失败）。
14. node 工具用托管绝对路径 `C:\Users\FIT\.workbuddy\binaries\node\versions\22.22.2-3\node.exe`。
15. ★ agent-browser 未装，不靠浏览器截图 → headless 实测尺寸。
16. ★★★ `Object3D.attach()` 保持世界变换 → 抬升在 attach 后设 `root.position`，验证量世界坐标。
17. ★★★ 漏 import 会穿到线上（vite/headless 都不报）→ `check_imports.mjs` 守卫。
18. 建运动链工厂 return 前必摆零位（否则首帧折臂误判穿地）。
19. 用户重复同一需求 = 上一版视觉不成立 → 解法靠辨识度非改大数字。
20. 查产物中文用 Grep 工具，勿 PowerShell（GBK 假阴性）。
21. ★ 给已有表加列要**新建表**（create_all 不补列）。
22. ★ 测试口令必须在 conftest.py **模块级**强制赋值（.env 会顶掉 fixture setdefault）。
23. ★ AppError 文案在 `message`、HTTPException 在 `detail`，断言两边都兜。
24. ★ 模块级全局状态测试间串味 → autouse fixture 复位。
25. ★ 采集器自动恢复/手动 reconnect 都要尊重 `simulate=always`。
26. ★ 沙箱后台进程会被回收；长跑验证用 TestClient 或"起进程+探测"同一条命令。
27. ★★ apiUrl/apiControl 自动补 `/api`，传 `/api/...` 会变 `/api/api/...`；新调用后 Grep 复查。
28. ★ favicon 内联 data-URI（`#`→`%23`）。
29. ★★★ 中文串嵌 ASCII 引号会白屏且 Vite/headless 都查不出 → `check_cjk_bare.mjs` 守卫；文案引号统一用「」。
30. ★★ 加数据必须同时改组件渲染（verify_about H 段双向断言）。
31. ★ 守卫有假 FAIL 就会被关掉 → 用 AST/结构化特征判，别按标题文字/标签形状。
32. ★★ 测试写真实配置时夹具必须备份/还原 + reload_config()。
33. ★ 全等断言遇集合合法变大先判"测试过期还是代码坏了"，更新时补语义断言。
34. ★ 改名三处锁：index.html title / brand.js / brand.py；FIT-RCMS 不变。
35. ★★ 控制器可读性按 (start,qty) 整块判断。
36. ★★ addr138..143 是**目标角**不是第二路读数；判断"读到真机"只看 10..21 在不在变。
37. ★★★ **绝不用"写 0 试探"**验证目标区（= 命令回全零位姿）。
38. ★ health 的 robot.mode=real 与 motion.mode=sim 会同时"正确"；能碰真机只看 motion.mode。
39. ★ 现场编号（200/411）在寄存器里找，不在系统对象里找。
40. ★★ GitHub 私有仓库网页 404 → 用 gh api；gh repo clone 会被杀。
41. ★★★ 控制器无关节级指令；点动=常驻程序+喂角+触发；WAIT 必须等读区；写后回读+静置 0.15s；速度在 40103。
42. ★★ 点动通道就绪后任何写 40135.Bit0 的一方都能动机器人 → 多上位机并存要约定单一控制者。
43. ★★ 回归断言按结构化特征判（3 个假 FAIL 教训：注释文字/传参方/v-for 形状）。

**沙箱/环境补充**：curl 带 `--noproxy "*"`（否则 502 代理拦截）；`/tmp` 路径无效写 `logs/`；PowerShell 工具启动后端用 Start-Process（cmd start 被拒"拒绝访问"）；bash 后台进程随命令结束被回收。

## 14. 用户偏好与协作方式

- **中文交流**；企业级交付标准——要求重构、重设计、持续优化、无缺陷闭环。
- 期望：**先出方案再执行**（大改动写方案文档到 `docs/`），中途不打断、不反复提问，一次性完成不卡壳。
- 每轮改动必须：跑全套守卫（verify/pytest/build）→ 全绿才交付；新增能力要配 headless 回归守卫钉死。
- 改动完成后更新 memory 日志。
- 安全红线：碰真机前确认；绝不"写 0 试探"；测试进程绝不写真机；围栏互锁不绕过。

## 15. 历史时间线（对话史浓缩）

| 阶段 | 内容 |
|---|---|
| 阶段0-2 | 基础监控 + Modbus 采集 + 控制令牌鉴权 + 点位/程序执行引擎 |
| 阶段3/4 | 事件总线审计 + 围栏互锁 + 角色(admin/operator) + 备份导入导出 + 围栏版本回滚 + 部署文档 |
| 阶段5 | 滑块示教(关节/直角) + J1~J6 点动(增量/连续+看门狗) + 残影预演 + 单文件执行 + 摄像头进执行页 + 真实监控只读化 + 全局快捷键 |
| 阶段6 | 权限门控 tabs.js + ExecutionView 拆分为 PointExec/ProgramExec + 侧栏 400px 迁移 + 底栏三盏灯/引导条重组 + runmode 档位声明 + 链路四态 |
| 阶段6+ | 「关于」页实验作业指导 + SettingsView 设置中心(10组×12控件) + 全局快捷键 + check_cjk_bare 守卫（中文引号白屏根治） |
| 现场联调 | 寄存器只读普查（reg_survey.py）+ 勘察报告 + 围栏状态片移 3D 视口右下角 |
| 阶段7 | 私有参考仓库拉取 → 控制模型纠正（无常驻指令，靠常驻程序）→ ready_up.py 一键就绪 → J6 实机点动 +1.000° 成功 → RobotParamsCard 三页共用 |
| Stage A | 姿态权威模型 resolvePose（三页实时同步、3D 跟随、底部三灯修复、setLocalDemo 灭绝） |
| 用户需求轮 | 标题去 AI（FIT-RCMS 保持）/ 控制权限默认不限时+设置页可配+重启失效 / 如实对账落地状态 |
| Stage C | SimRobot 有状态仿真机（模拟分支指令走位）+ WS cmd/cmd_tcp/tracking |
| Stage D | modbus.py 写路径按实测寄存器表重写（FC6+io锁+回读校验+settle）+ rc_ready 一键就绪 + /api/rc-status + /api/ready + 档位语义反转（用户实机纠正）+ 双闸开启 |
| 收尾 | PowerShell 重启后端 → Web J6 点动 ±1° 实测成功 → RcReadyCard 真机链路就绪卡上线（本交接文档同日生成） |

## 16. 待办与下一步

1. **前端 RcReadyCard 已上线**，后续可考虑把 rc-status 并入 WS 推流（省一路轮询）。
2. 方案 P1/P2 缺陷清单（Task #120）部分已顺带解决（P1-2 真值表、P1-10、P2-5/6/23），其余未动。
3. DH 参数仍 `calibration_pending`（J5/J6 α、d6 为估算）——影响 TCP 精度不影响点动。
4. 未实施方案：`docs/方案-示教器坐标系与点动模式适配.md`（P3 坐标系接口→P4 直角点动求解器）、R14 截图项未确认。
5. 视觉自动执行 `vision.auto_execute` 保持 false（误判+自动运动=事故）。
6. 直角坐标点动（雅可比降速）与工具/用户坐标系尚无实现。

## 17. 常用命令速查

```bash
# 登录取令牌
curl -s --noproxy "*" -X POST http://127.0.0.1:8000/api/auth/login -H "Content-Type: application/json" -d '{"password":"admin123"}'
# 读真机状态（公开只读）
curl -s --noproxy "*" http://127.0.0.1:8000/api/rc-status
curl -s --noproxy "*" http://127.0.0.1:8000/api/pose        # j1..j6 平铺
# 声明档位 / 一键就绪 / 点动（都要 X-Control-Token）
curl -s --noproxy "*" -X POST http://127.0.0.1:8000/api/control/run-mode -H "X-Control-Token: $TOKEN" -H "Content-Type: application/json" -d '{"mode":"AUTO"}'
curl -s --noproxy "*" -X POST http://127.0.0.1:8000/api/ready -H "X-Control-Token: $TOKEN" -H "Content-Type: application/json" -d '{}'
curl -s --noproxy "*" -X POST http://127.0.0.1:8000/api/control/jog/step -H "X-Control-Token: $TOKEN" -H "Content-Type: application/json" -d '{"joint":6,"dir":1,"angle_deg":1,"speed_dps":15}'
# 寄存器只读勘察（绝不写）
.venv/Scripts/python.exe scripts/reg_survey.py watch 60
```

## 18. 关键文件索引

| 文件 | 内容 |
|---|---|
| `backend/app/services/modbus.py` | 实测寄存器表常量 + FC6 写路径 + rc_jog_execute/rc_snapshot/rc_command/rc_ready 全部原语 |
| `backend/app/services/rc_ready.py` | 一键就绪 8 步（安全铁律全在 docstring） |
| `backend/app/services/motion.py` | 双闸真值表 + command() 真实/模拟分支 |
| `backend/app/services/runmode.py` | 档位语义反转 JOGGABLE |
| `backend/app/api/robot.py` | /api/rc-status + /api/ready |
| `config/robot.yaml` | 连接/DH/限位/点动参数/寄存器注释（已证伪地址留档） |
| `docs/控制器Modbus寄存器勘察报告.md` | 实测确认版寄存器报告 |
| `frontend/src/stores/rcReady.js` + `components/RcReadyCard.vue` | 真机链路就绪卡 |
| `frontend/src/stores/poseAuthority.js` | 姿态权威纯函数 |
| `D:\EFORT_Projects\_ref_efort_panel\` | 参考实现本地副本（efort_client.py / ready_up.py / jog_axis.py） |
| `C:\Users\FIT\WorkBuddy\2026-09-19-02-25-43\.workbuddy\memory\` | 模型侧记忆（MEMORY.md + 每日日志 2026-09-23.md 最全） |
