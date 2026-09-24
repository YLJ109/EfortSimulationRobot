# EFORT Web Monitoring

埃夫特 **ER8-700H** 六轴工业机器人 · **全栈网页端三维实时监控与仿真系统**。

后端通过 Modbus TCP 只读机器人关节角 (J1–J6) → 正运动学计算 TCP → WebSocket 实时推送；
前端用 Three.js 按 DH 参数驱动 **官方 STEP 数模导出的 GLB**，真实还原机器人形态并实时跟随。
内置工业安全围栏（区域越界 + 地面碰撞四级报警）、实验室场景、离线模拟演示、录制回放与视觉检测。

---

## 一、功能特性

| 模块 | 能力 |
|---|---|
| **真实监控** | Modbus TCP 20Hz 推送，关节角 + TCP + 速度实时显示；机器人离线时自动切换为前端本地演示（不中断画面） |
| **模拟仿真** | 6 轴滑条 + 键盘精确操控；关节/机器人两种操控模式（关节角 ↔ 世界坐标 XYZ/姿态）；正逆运动学预演 |
| **录制回放** | 从真机或仿真录制轨迹入库，时间轴拖动/变速播放，支持导出/导入 JSON |
| **视觉检测** | 海康 MV-CU120-10GM GigE 工业相机 MJPEG 实时流 + YOLO 目标检测，可截图存档；检测模型按需加载、可一键释放内存；以**悬浮小面板**内嵌于「真实监控」右上角，可展开/收起 |
| **安全围栏** | 矩形 / 任意四边形 / 圆形多区域，各自四级阈值（安全/接近/危险/碰撞）；围栏越界 + 机器人最低点离台面高度双维度检测；玻璃墙、角柱、黄色警示带的完整工业化视觉；配置持久化 + 报警事件时间线 |
| **实验室场景** | 14m × 14m 车间房间（四面墙 / 天花板 / 灯带 / 点光 / 10 组贴墙设备道具）+ 3.6m 环氧自流平工作区 + 拉丝不锈钢设备站台 |
| **智能剖切** | 相机移到房间外侧或屋顶上方时，挡视线的那一面墙 / 天花板自动变半透明（不消失），其余保持实体 |
| **三维姿态平滑** | 真机 20Hz 的跳变被指数平滑成连续运动，视觉上不抖 |

---

## 二、系统架构

```
┌──────────────────────┐  Modbus TCP (FC3, reg10-21, IEEE754 字序交换)
│  Robox 控制器         │◄──────────────── 只读，不写入任何寄存器
│  192.168.1.12:502    │
└──────────────────────┘
            │ J1..J6 (deg)
            ▼
┌──────────────────────────────────────────────────────────────┐
│ backend  FastAPI + uvicorn            :8000                   │
│   services/modbus.py    读寄存器(失败→重连退避)                 │
│   services/kinematics.py 正运动学 FK → TCP 位姿                 │
│   services/collector.py  采样线程: 入库(节流) + 广播             │
│   services/hub.py        WebSocket 连接管理                     │
│   api/  robot / control / recordings / safety / ws              │
│   db/   SQLite(WAL)  pose_history / events / sessions /        │
│                       recordings / safety_events                │
│   同时托管 frontend/dist 静态文件(no-cache)                     │
└──────────────────────────────────────────────────────────────┘
      │ ws://host:8000/ws/pose   {j1..j6, tcp, simulated, t}
      │ http://host:8000/api/*   REST
      ▼
┌──────────────────────────────────────────────────────────────┐
│ frontend  Vue 3 + Pinia + Three.js (单 WebGLRenderer/单场景)   │
│   3 个 Tab 用 v-show 切换 —— 3D 场景常驻，不重建、不闪断        │
└──────────────────────────────────────────────────────────────┘
      │ http://host:8100/{stream,snapshot,status,...}
      ▼
┌──────────────────────────────────────────────────────────────┐
│ camera  camera_service.py (独立进程)   :8100                   │
│   海康 MVS SDK 抓帧 → numpy/PIL → MJPEG；YOLO 按需加载检测      │
└──────────────────────────────────────────────────────────────┘
```

**端口占用**

| 端口 | 服务 | 必需 |
|---|---|---|
| 8000 | 后端 API + WebSocket + 前端静态托管 | 是 |
| 8100 | 视觉检测服务（海康相机 + YOLO） | 否（「真实监控」内的摄像头面板需要） |
| 5173 | Vite 开发服务器 | 否（仅前后端分离开发时） |
| 502 | 机器人 Modbus TCP（外部设备） | 否（连不上自动回退模拟） |

---

## 三、环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| **Python** | 3.10+（实测 3.13） | 后端；`setup.bat` 自动建 `backend/.venv` |
| **Node.js** | 20+（实测 22） | 前端构建（Vite） |
| 操作系统 | Windows 10/11 | 启动脚本为 `.bat`；后端/前端本身跨平台 |
| 浏览器 | Chrome / Edge 现代版本 | 需支持 WebGL2 与 ES2020 |
| MVS 客户端 | 海康机器人官方 | **仅视觉检测需要**；提供相机 DLL 与 Python 封装 |

> 视觉检测用的是**另一套解释器**（含 PyTorch / ultralytics / MVS 封装），
> 与后端 venv 分开，避免把数 GB 的 torch 塞进后端环境。见「环境变量」。

---

## 四、快速开始

### 4.1 首次安装（一键）

双击根目录 **`setup.bat`**，它会依次完成：

1. 检查 Python / Node 是否就位（版本不达标会明确报错）
2. 创建 `backend\.venv` 并安装 `backend/requirements.txt`
3. 在 `frontend\` 安装 npm 依赖（有 lock 文件时用 `npm ci`）
4. 创建运行时目录 `data\` `logs\` `camera\captures\`
5. 构建前端到 `frontend\dist`
6. 若 `.env` 不存在，从 `.env.example` 复制一份

脚本幂等，可反复运行。

**环境自检**：双击 `scripts\doctor.bat`，逐项列出 PASS / WARN / FAIL 并给出修复提示。

### 4.2 启动

```
run.bat              清理旧进程 + 启动视觉服务 + 后端 + 打开浏览器(推荐)
run.bat backend      只启动后端 :8000
run.bat camera       只启动视觉检测服务 :8100
run.bat stop         停止 8000 / 8100 上占用端口的进程
run.bat build        重新构建前端
run.bat help         查看用法
```

浏览器访问 **http://localhost:8000**

`scripts\start_all.bat`、`scripts\start_backend.bat`、`scripts\start_camera.bat`、
`scripts\build_frontend.bat` 是等价的单功能入口，便于做快捷方式或计划任务。

### 4.3 开发模式（前后端分离，热更新）

```
scripts\start_backend.bat       终端 1：后端 :8000
scripts\start_frontend_dev.bat  终端 2：Vite :5173
```

打开 http://localhost:5173 —— Vite 已把 `/api` 与 `/ws` 代理到 8000，同源免 CORS。

### 4.4 构建产物

`frontend/dist` 由后端以 `no-store` 托管。改完前端执行 `run.bat build`，
浏览器**直接刷新**即可看到新版本（已禁用缓存，无需强刷）。

---

## 五、目录结构

```
EFORT_Web_Monitoring/
├─ setup.bat                 一键安装（Python/Node 依赖 + 构建）
├─ run.bat                   一键启动 / 停止 / 构建
├─ .env.example              环境变量模板（复制为 .env 生效）
├─ config/
│   ├─ robot.yaml            ★ 唯一数据源：DH / 寄存器 / 采样 / 限位 / 安装方式
│   └─ safety.json           安全围栏配置（运行时可改，原子写落盘）
├─ backend/                  FastAPI 后端（独立 venv）
│   ├─ app/
│   │   ├─ main.py           入口：路由挂载 + 静态托管 + 生命周期
│   │   ├─ api/              薄路由层 robot / control / recordings / safety / ws
│   │   ├─ core/             config(含 .env 加载) / deps / exceptions / logger / middleware / safety_config
│   │   ├─ db/                models / database(引擎+WAL) / crud
│   │   ├─ schemas/          Pydantic 出入参
│   │   └─ services/         modbus / kinematics / collector / hub
│   ├─ scripts/init_db.py    手工建库
│   ├─ tests/                pytest（API / 运动学 / 安全围栏）
│   ├─ requirements.txt      运行依赖
│   └─ requirements-dev.txt  附加 pytest / httpx
├─ camera/                   视觉检测服务（独立进程 + 独立解释器）
│   ├─ camera_service.py     标准库 http.server：MJPEG / 抓帧 / YOLO
│   ├─ models/               .pt 模型目录（yolo26n.pt）
│   ├─ captures/             截图输出
│   └─ requirements.txt      仅视觉侧依赖（numpy / pillow / ultralytics）
├─ frontend/                 Vue 3 + Three.js
│   ├─ index.html            页面骨架 + 全部 CSS（深色工业主题）
│   ├─ vite.config.js        base "./" + dev proxy
│   ├─ public/models/        robot_full.glb ← 官方 STEP 数模导出件
│   ├─ src/
│   │   ├─ main.js           Vue 应用装配
│   │   ├─ config.js         WS/API 地址推断 + 兜底 DH + 相机地址
│   │   ├─ App.vue           3 Tab 切换 + 报警条 + 状态片
│   │   ├─ three/
│   │   │   ├─ manager.js    ★ 单场景管理器：模型装配 / 姿态平滑 / 视角
│   │   │   ├─ safety.js     ★ 围栏引擎：多边形间距 / 四级分级 / 报警垫
│   │   │   ├─ lab.js        ★ 实验室房间 + 智能剖切
│   │   │   └─ cell.js       ★ 工作区地坪 + 设备站台（黄黑警示带）
│   │   ├─ components/       MonitorLayout / RealMonitor / SimMonitor /
│   │   │                    RecordingView / CameraPanel / SafetyPanel / JointHud
│   │   ├─ stores/           robot / recording / camera / safety
│   │   └─ utils/            dance(离线演示轨迹) / alarm(Web Audio 蜂鸣) / safetyConfig(归一化)
│   └─ tools/                ★ 无头回归测试（见「测试」）
├─ docs/                     方案与设计文档
├─ assets/cad/               官方手册 / 运动范围图 / 数模原件
├─ data/                     robot.db（运行时生成）+ STEP 数模原件
└─ logs/                     运行日志
```

---

## 六、配置

### 6.1 机器人配置 `config/robot.yaml`

机型相关的**唯一数据源**，换机型只改这一个文件：

| 段 | 作用 |
|---|---|
| `robot` | 型号 / 序列号 / 负载 / 臂展（信息展示） |
| `connection` | Modbus 主机、端口、unit_id、超时、`simulate`(auto/always/never) |
| `modbus` | 功能码、起始寄存器、数量、单位、字序（`swap` = 高字在后） |
| `axis_sign` | 6 轴符号，某轴方向与真机相反时改成 `-1`（全链路一致生效） |
| `joint_limits` | 关节行程限位（前端 clamp + 量程显示） |
| `dh` | **DH 参数**：`d / a / alpha / theta_offset`，决定三维形态与 FK 精度 |
| `mounting` | 安装方式 `floor / wall / ceiling` |
| `sampling` | 读取频率、入库频率、推送频率、历史保留天数 |
| `database` / `server` | 库位置（可被环境变量覆盖）/ 监听地址端口 |

### 6.2 安全围栏 `config/safety.json`

运行时可改，通过 API 或界面「安全围栏」面板保存（原子写 + 强校验）。

```jsonc
{
  "enabled": true,
  "zones": [{
    "id": "z1", "name": "主工作区", "enabled": true,
    "shape": "rect",                 // rect | quad | circle
    "center": { "x": 0, "z": 0 },    // rect / circle 圆心
    "half":   { "x": 0.82, "z": 0.82 },  // rect 半宽(m)
    "radius": 0.9,                   // circle 半径(m)
    "corners": [[-0.82,-0.82], ...], // quad 四个角点(m)
    "height": 1.2,                   // 围栏高度(m)
    "walls": true,                   // 该区域是否显示玻璃墙
    "posts": { "enabled": true, "size": 0.1, "color": "#f2c500" },
    "thresholds": {
      "basis": "halfwidth",          // halfwidth(中心到最近边) | fixed_mm(绝对值)
      "warn": 0.3, "danger": 0.1,    // 占用比例阈值
      "fixed_mm": 300.0              // basis=fixed_mm 时的基准
    },
    "colors":  { "safe": "#2ecc71", "warn": "#f2c500", "danger": "#e5484d", "hit": "#ff2020" },
    "opacity": { "safe": 0.11, "warn": 0.2, "danger": 0.3, "hit": 0.45 }
  }],
  "blink": { "hz": 4.0, "min": 0.18, "max": 0.63 },      // 碰撞闪烁
  "alarm": { "banner": true, "chip": true, "banner_min": "danger", "sound": false },
  "walls": true,                       // 全局玻璃墙总开关（关掉仍保留线框+角柱）
  "ground": {                          // 地面/台面碰撞检测
    "enabled": true,
    "warn_mm": 150, "danger_mm": 80, "hit_mm": 30,       // 必须满足 hit ≤ danger ≤ warn
    "colors":  { ... }, "opacity": { ... }
  },
  "overlay": { "bbox": false },        // 显示围栏包围盒线框
  "camera":  { "position": [...], "target": [...] }       // 保存的视角
}
```

> **地面报警垫的形状跟随第一个启用区域的轮廓**（矩形/四边形/圆形均可），
> 机器人站在设备站台上时，零点自动对齐**台面**而非房间地坪，
> 读数含义是「机器人最低点离台面的高度」。

---

## 七、环境变量

复制 `.env.example` 为根目录 `.env`。**已存在的系统环境变量优先，`.env` 不会覆盖它**；
不建 `.env` 时全部使用内置默认值。

| 变量 | 默认 | 作用 |
|---|---|---|
| `EFORT_SIMULATE` | `auto` | `auto` 连不上自动回退模拟 / `always` 强制模拟 / `never` 强制真机 |
| `EFORT_DB_URL` | `sqlite:///data/robot.db` | 覆盖数据库连接串（测试/部署用） |
| `CAMERA_PORT` | `8100` | 视觉检测服务端口 |
| `EFORT_CAMERA_PYTHON` | 参考项目 venv 路径 | 跑视觉服务的解释器（需含 torch/ultralytics/MVS 封装） |
| `EFORT_MVS_SDK_DIR` | 参考项目 MvImport 路径 | `MvCameraControl_class.py` 所在目录 |
| `EFORT_MVS_RUNTIME_DIR` | MVS 标准安装路径 | MVS 运行时 DLL 目录 |

前端构建期变量放 `frontend/.env`（模板见 `frontend/.env.example`），改动后需重新构建：

| 变量 | 默认 | 作用 |
|---|---|---|
| `VITE_CAMERA_PORT` | `8100` | 前端请求视觉服务的端口 |

---

## 八、API 一览

### 8.1 机器人 / 姿态（`/api`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 存活探针（不依赖 DB / 机器人） |
| GET | `/api/version` | 服务版本信息 |
| GET | `/api/health` | 机器人连接状态、模拟标志、采样信息 |
| POST | `/api/reconnect` | 手动触发 Modbus 重连 |
| GET | `/api/pose` | 当前姿态（关节角 + TCP + 时间戳） |
| GET | `/api/history?limit=500` | 历史姿态 |
| GET | `/api/export/csv?limit=2000` | 导出 CSV |
| GET | `/api/meta` | 机型 / DH / 标定状态 / 关节限位 |
| **WS** | `/ws/pose` | 实时姿态推送 |
| GET | `/docs` | Swagger 交互文档 |

### 8.2 控制 / 运动学（`/api/control`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/control/limits` | 关节限位 + 可达半径 |
| POST | `/api/control/preview` | **指令预演**：只算不下发，返回末端位姿与逐段校验 |
| POST | `/api/control/ik` | 逆运动学求解（轻量端点，不记请求日志） |

> 本系统对机器人**只读**。`/preview` 与 `/ik` 都是纯计算，不会写入控制器。

### 8.3 录制（`/api/recordings`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `` | 列表 |
| POST | `` | 新建（带 `source`: `real` / `sim`） |
| GET | `/{id}` | 详情（含完整帧序列） |
| PUT | `/{id}` | 改名 / 更新 |
| DELETE | `/{id}` | 删除 |
| GET | `/{id}/export` | 导出 JSON |
| POST | `/import` | 导入 JSON |

### 8.4 安全围栏（`/api`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/safety` | 读取当前配置 |
| PUT | `/api/safety` | 保存（原子写 + 强校验，非法返回 `SAFETY_CONFIG_INVALID`） |
| POST | `/api/safety/reset` | 恢复出厂 |
| GET | `/api/safety/default` | 取默认配置 |
| POST | `/api/safety/validate` | 只校验不落盘 |
| GET / POST / DELETE | `/api/safety/events` | 报警事件时间线 |

### 8.5 视觉检测服务（`:8100`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stream` | MJPEG 实时流（含检测框） |
| GET | `/snapshot` | 当前单帧 JPEG |
| POST | `/snapshot-save` | 保存到 `camera/captures/` |
| GET | `/status` | 状态（设备 / FPS / AI / 检测耗时 / 模型列表） |
| GET | `/models` | 可用 `.pt` 模型列表 |
| POST | `/open` `/close` | 打开 / 关闭相机（异步，前端轮询 `/status`） |
| POST | `/reconnect` | 手动重连相机（网口切换后无需重启服务） |
| POST | `/config` | 更新 AI 配置 `enabled / model / conf / imgsz` |
| POST | `/unload` | 卸载 YOLO 模型并回收内存 |

---

## 九、界面操作

### 9.1 三个 Tab

| Tab | 内容 |
|---|---|
| **真实监控** | 实时关节 HUD、TCP 位姿、连接状态；离线时自动播放本地演示轨迹；右上角内嵌**摄像头悬浮面板**（小画面 + 可展开/收起） |
| **模拟仿真** | 关节滑条 / 世界坐标操控、指令预演、🕺 跳舞演示 |
| **录制回放** | 录制列表、时间轴播放/暂停/变速、导入导出 |

#### 摄像头悬浮面板（真实监控右上角）

- 默认**收起**：只显示小画面缩略图 + 状态点 + 帧率，不占地方；
- 点标题栏**展开**：显示完整控制 —— 打开/关闭相机、↻ 重连相机、启用 YOLO 检测、
  模型选择、置信度/推理尺寸调节、截图存档、释放模型内存；
- 相机画面统一按比例缩放（`object-fit: contain`），不放大不裁切；
- 相机服务（`:8100`）由 `run.bat` 或 `run.bat camera` 启动，服务未运行时会提示。

### 9.2 快捷键（仅「模拟仿真」Tab 生效）

| 按键 | 作用 |
|---|---|
| `1`–`6` | 选中关节 J1–J6（机器人模式下 `1`–`3` 选 XYZ） |
| `↑` / `↓` | 步进调整选中轴（关节 ±1° / XYZ ±10mm） |
| `Shift` + `↑`/`↓` | 微调（关节 ±0.1° / XYZ ±1mm） |
| `0` | 归零 |

### 9.3 安全围栏面板

在任意视图右侧，可实时调整区域形状/尺寸/阈值、四级颜色与透明度、
地面碰撞阈值、报警方式（横幅/状态片/蜂鸣）、视角存取，并查看报警事件时间线。
改动**即时生效**并在 3D 场景中同步；未保存时面板显示 `dirty` 标记。

---

## 十、测试与回归

### 10.1 前端（无头，无需浏览器）

```
cd frontend
npm run check     只跑跨模块常量导入检查
npm run verify    导入检查 + 全部回归套件
```

| 套件 | 覆盖 | 用例数 |
|---|---|---|
| `check_imports.mjs` | **静态导入守卫**：用了别的文件 `export` 的常量却没 import | 23 文件 |
| `verify_lab.mjs` | 房间尺寸 / 贴墙道具不穿墙不沉地 / 照明 / 智能剖切 | 44 |
| `verify_cell.mjs` | 站台七层几何 / 台面顶面高度 / 报警垫必须铺在台面上 | 52 |
| `verify_ground.mjs` | 四级分级边界 / 缺 colors 时的**按状态兜底取色** / 报警垫形状 | 67 |
| `verify_safety.mjs` | 多边形间距 / 圆形间距 / 阈值迁移 / 逐面墙着色 | 49 |
| `verify_official_model.mjs` | **官方数模装配自检**窗口 + `attach()` 提升顺序双路对照 + 零件归类 | — |
| `verify_kinematics.mjs` | 前端 three 链 vs 后端 FK 逐点对拍；`attach()` 保持世界变换 | — |

> `npm run build` **已内置** 导入检查：缺 import 会在构建阶段就失败，
> 不会再出现「构建通过、浏览器运行时报 `ReferenceError`」的情况
> （rollup 把未声明标识符当全局变量，vite build 本身不会报错）。

### 10.2 后端

```
cd backend
.venv\Scripts\activate
pytest                     # 需要 requirements-dev.txt
```

覆盖：API 形状与错误格式、正逆运动学一致性、安全围栏配置校验与事件接口。

---

## 十一、部署

### 11.1 单机交付（推荐）

`setup.bat` → `run.bat`。后端同时托管前端静态资源，**只需开放 8000**。

### 11.2 局域网访问

后端默认监听 `0.0.0.0:8000`。同网段其他机器访问 `http://<本机IP>:8000` 即可；
若启用了视觉检测，还需放通 8100（前端会按当前页面的 hostname 拼 8100 地址）。

Windows 防火墙放通示例（管理员 PowerShell）：

```powershell
New-NetFirewallRule -DisplayName "EFORT Web 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
New-NetFirewallRule -DisplayName "EFORT Vision 8100" -Direction Inbound -Protocol TCP -LocalPort 8100 -Action Allow
```

### 11.3 开机自启

把 `run.bat` 的快捷方式放入 `shell:startup`，或在任务计划程序中新建「登录时」触发的任务，
程序填 `run.bat`、起始位置填项目根目录。

### 11.4 停止

`run.bat stop` 会结束占用 8000 / 8100 的进程。

---

## 十二、故障排查

| 现象 | 排查 |
|---|---|
| 页面打不开 | 先跑 `scripts\doctor.bat`；确认 8000 在监听、`frontend\dist\index.html` 存在 |
| 界面改了但页面没变 | `run.bat build`，然后**普通刷新**即可（静态资源已 `no-store`） |
| 3D 里是方块模型不是真机外观 | 官方 GLB 加载或**装配自检失败**已回退程序化模型。按 F12 看 Console：成功会打印 `[官方数模] 已按 DH 关节轴重挂 N 个零件…`，并附带实测尺寸与零件归类 |
| 关节角一直不变 / 显示"模拟" | 机器人未连通。检查 `config/robot.yaml` 的 host/port、网线是否在 `192.168.1.x` 网卡；`EFORT_SIMULATE=always` 时为预期行为 |
| 姿态与真机方向相反 | 改 `config/robot.yaml` 的 `axis_sign` 对应位为 `-1` |
| 视觉 Tab 提示"相机服务未启动" | 未启动 8100 服务，或端口被 `CAMERA_PORT` 改了；`run.bat camera` 启动 |
| 相机打不开（0x8000…） | 相机被 MVS 客户端占用；或网线不在相机网段。关掉 MVS 后点「重连」即可，无需重启服务 |
| 视觉服务内存涨到数 GB | PyTorch 加载后常驻属正常。取消勾选「启用检测」或点「释放模型内存」会卸载模型并裁剪工作集 |
| 后端日志 `database is locked` | 已启用 WAL + `busy_timeout=5000`；若仍出现，检查是否有别的进程独占 `data/robot.db` |
| `.env` 不生效 | 同名**系统环境变量优先级更高**；`doctor.bat` 会打印实际生效值 |

---

## 十三、数模与标定状态（已知限制）

### 13.1 官方数模

`frontend/public/models/robot_full.glb` 由官方 STEP 数模转换而来，
按 DH 关节轴拆分为 6 个连杆 + 底座，并挂了 EFORT 红色标识。
装配完成后会做**几何自检**（高/前后跨度/左右跨度/底面高度四项），
任一项超窗即视为装配错误：**卸掉半成品 → 回退程序化模型 → 控制台告警**，
保证页面永不出现"散架"的官方模型。

用 `frontend/tools/verify_official_model.mjs` 可离线复核自检窗口与零件归类。

### 13.2 DH 标定

`config/robot.yaml` 中：

- **已实测可信**：`a1=49.5932`、`a2=330.1834`、`a3=40.2714`、`d2=0.2852`、`d4=329.2414`（来自装箱标定表）
- **仍为估算**：`d1`（基座高）、`d6`（腕长）、部分 `alpha` 角 —— `calibration_pending: true`

校准步骤：

1. 从埃夫特官网下载中心下载《ER8-700H 机器人数模》《运动范围图》《机械使用维护手册》，
   放入 `assets/cad/`（STEP/DWG 原件在 `data/`）。
2. 用运动范围图/手册核对 `d1`、`d6` 与 `alpha`，更新 `config/robot.yaml`。
3. 用全 0°、J1=90°、J4=90° 等已知姿态做目视交叉校验。
4. 前后端 DH 必须同步：前端 `src/config.js` 的 `FALLBACK_DH` 是 `/api/meta` 不可用时的兜底值。
5. 回归验证：`npm run verify`（含 three 链与后端 FK 的逐点对拍）。

---

## 十四、技术栈

**后端**：Python 3.13 · FastAPI · uvicorn · SQLAlchemy 2.0 · SQLite(WAL) · websockets · numpy · PyYAML
**前端**：Vue 3 (`<script setup>`) · Pinia · Vite · Three.js · Web Audio
**视觉**：海康 MVS SDK (GigE) · numpy · Pillow · Ultralytics YOLO (PyTorch)
**三维**：官方 STEP → GLB（`step-to-glb` 流程，基于 occt-import-js / OpenCascade WASM）
