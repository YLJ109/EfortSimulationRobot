// =====================================================================
// Pinia store：执行域（示教 / 点动 / 点位库 / 程序执行 / 按文件名执行 / 急停）
//
// ★ 为什么把逻辑从组件里搬出来：
//   "点位执行"与"程序执行"是两页，但底层完全是同一套东西（控制权限、围栏互锁、
//   速度、残影预演、急停、后端接口）。放在 store 里 = 一份实现、两处入口；
//   如果各写一份，改安全判据时必然漏掉一边 —— 这是最容易出事故的地方。
//
// 安全规则（前端先拦，后端再校验一次）：
//   1. 写操作必须持有管理员控制令牌（auth.controlActive），否则弹登录。
//   2. **点位 / 示教 / 程序**：按**残影预演的目标位姿**判定 —— 残影不撞、不泛红才放行
//      （gateAt 在下发前同步量一次，不依赖"用户先悬停过"）。
//      ★ 不按实体机**当前位姿**拦：它现在在哪与"要去哪"无关，用它会拦掉"从危险区撤出来"
//        这种正当动作。实时位姿只在**点动**上仍作判据（点动是就地增量走，没有目标可预演）。
//   3. 真实下发默认关闭；未开启时后端只做校验 + 模拟动画。
//   4. 点动是独立线程，必须先 stop 再急停（后端 /control/estop 内部已处理）。
//
// 定时器必须放模块作用域而不是 state：Pinia 的 state 会被代理/序列化，
// 计时器句柄放进去既没意义又容易被响应式系统搅乱。
// =====================================================================
import { defineStore } from "pinia";
import { apiUrl } from "../config.js";
import { apiControl } from "../net/control.js";
import { useAuthStore } from "./auth.js";
import { useRobotStore } from "./robot.js";
import { useSafetyStore } from "./safety.js";
import { setGhostPose, showGhost, highlightJoint, evalSafety } from "../three/manager.js";
import { STATE_RANK } from "../three/safety.js";

export const JOG_STEPS = [0.1, 1, 5, 10];
const HOLD_MS = 260;      // 按住超过该时长 → 连续点动；否则视为单击走一格
const LOG_MAX = 240;

let previewTimer = null;
let keepAliveTimer = null;
let pollTimer = null;
let pressTimer = null;
let pressInfo = null;

const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

function hhmmss() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

export const useExecStore = defineStore("exec", {
  state: () => ({
    // ---- 数据 ----
    points: [],
    programs: [],
    files: [],
    pointsErr: "",
    programsErr: "",

    // ---- 公共执行参数 ----
    // ★ 全局执行速度：默认 5%（安全档位，避免误发时机器人窜太快）。
    //   顶栏滑块调整后经 saveSpeed 持久化，页面刷新后保持用户设过的值。
    speed: (() => {
      const v = parseInt(
        (typeof localStorage !== "undefined" && localStorage.getItem("exec.speed")) || "5", 10
      );
      return isFinite(v) && v >= 1 && v <= 100 ? v : 5;
    })(),
    dryRun: true,
    fileName: "",
    fileBusy: false,
    fileResult: null,

    // ---- 滑块示教 ----
    teachMode: "joint",                       // joint | cartesian
    teachQ: [0, 0, 0, 0, 0, 0],
    teachTcp: { x: 300, y: 0, z: 700 },
    teachSpeed: 5,                        // 示教/滑块下发速度（%）：★ 默认压到最慢 5，安全
    teachBusy: false,
    teachResult: null,
    teachGhost: true,

    // ---- 点动 ----
    jogSpeed: 5,                              // °/s（后端再夹到 max_speed_dps）：★ 默认最慢 5°/s，安全
    jogStepDeg: 1,
    jogBusy: false,
    jogState: null,
    holding: -1,                              // 当前按住的轴 1..6，-1 未按住
    holdingDir: 0,

    // ---- 急停 / 引擎 ----
    estopState: null,
    estopBusy: false,
    execState: null,

    // ---- 程序执行运行态 ----
    running: false,
    runAbort: false,
    runBusy: false,
    runName: "",
    runKind: "",                              // program | file
    runStep: 0,
    runTotal: 0,
    runDry: true,
    lastRun: null,
    /**
     * 最近一次被运行的"点位序列"程序对象。
     * ★ 只给快捷键（空格）用：空格键不应该自己去列表里猜一个程序运行 —— 那是隐式选择，
     *   现场会变成"按了空格不知道跑了哪个"。所以只在用户**主动跑过一次之后**，
     *   空格才有可重复的目标；从没跑过就只提示一句，不自己挑。
     */
    lastRunTarget: null,
    log: [],
  }),

  getters: {
    /** 关节限位（后端未返回时用兜底值，保证滑块永远能拖）。 */
    limits(s) {
      const l = useRobotStore().limits;
      return (l && l.length === 6)
        ? l
        : [{ min: -170, max: 170 }, { min: -170, max: 90 }, { min: -85, max: 150 },
           { min: -180, max: 180 }, { min: -115, max: 115 }, { min: -360, max: 360 }];
    },
    /**
     * 实体机**当前位姿**是否危险（实时读数）。
     * ★ 只给点动用：点动是"就地增量走"，没有目标位姿可预演，只能看现在在哪。
     */
    UNSAFE() {
      return ["danger", "hit"].includes(useSafetyStore().lastState);
    },
    /**
     * 残影**预演的目标位姿**是否危险 —— 点位 / 示教 / 程序的拦截依据。
     * ★ 现场口径：这三类操作**不按实体机当前位姿拦截**（它现在在哪与"要去哪"无关），
     *   只看残影预演：不撞、不泛红，就允许运动。
     */
    ghostUnsafe() {
      const s = useSafetyStore();
      return s.lastSource === "ghost" && ["danger", "hit"].includes(s.lastState);
    },
    canExec() {
      return useAuthStore().controlActive && !this.ghostUnsafe;
    },
    /** 点动按钮的可用性：与点位/示教分开判据（点动没有目标预演）。 */
    canJog() {
      return useAuthStore().controlActive && !this.UNSAFE;
    },
    /** 禁止下发的具体原因（给界面显示用，避免用户猜）。 */
    blockReason() {
      const s = useSafetyStore();
      if (this.ghostUnsafe) return `残影预演的目标位姿处于 ${s.lastState}，已禁止下发`;
      if (!useAuthStore().controlActive) return "未获得控制权限，无法下发";
      return "";
    },
    jogActive: (s) => !!(s.jogState && s.jogState.active),
    runPct(s) {
      if (!s.runTotal) return 0;
      return Math.max(0, Math.min(100, Math.round((s.runStep / s.runTotal) * 100)));
    },
    jointsSummary() {
      return (p) => (p && p.joints ? p.joints : []).map((v) => Number(v).toFixed(0)).join(", ");
    },
    /**
     * 示教器（控制器）程序：后端 /control/files 里 group="teach" 的候选，
     *   即 programs 目录下的 .XPL 文件（200/JOGSVC、411 等示教器导出的程序）。
     */
    teachFiles: (s) => (s.files || []).filter((f) => f.group === "teach"),
    /**
     * 本地程序（非示教器）：数据库预设点位 + programs 目录下的 .json 文件。
     *   ★ 数据库「点位序列程序」不在 files 里二次出现（它在 exec.programs），
     *     前端用 localItems 统一去重合并。
     */
    localFiles: (s) => (s.files || []).filter((f) => f.group === "local"),
  },

  actions: {
    // ================= 日志 =================
    logLine(level, text) {
      this.log.push({ t: hhmmss(), level, text: String(text) });
      if (this.log.length > LOG_MAX) this.log.splice(0, this.log.length - LOG_MAX);
    },
    clearLog() { this.log = []; },

    // ================= 全局执行速度持久化 =================
    saveSpeed() {
      try { localStorage.setItem("exec.speed", String(this.speed)); } catch (e) { /* 忽略 */ }
    },

    // ================= 权限 =================
    needAuth() {
      const auth = useAuthStore();
      if (auth.controlActive) return false;
      if (auth.requestLogin) auth.requestLogin();
      return true;
    },

    // ================= 数据加载 =================
    async loadPoints() {
      try {
        const r = await fetch(apiUrl("/points"));
        if (r.ok) { this.points = await r.json(); this.pointsErr = ""; }
        else this.pointsErr = "点位加载失败：" + r.status;
      } catch (e) { this.pointsErr = "点位加载失败：" + e.message; }
    },
    async loadPrograms() {
      try {
        const r = await fetch(apiUrl("/programs"));
        if (r.ok) { this.programs = await r.json(); this.programsErr = ""; }
        else this.programsErr = "程序加载失败：" + r.status;
      } catch (e) { this.programsErr = "程序加载失败：" + e.message; }
    },
    async loadFiles() {
      // ★ 未持有效令牌时不发请求：/control/files 在后端是 require_control，
      //   必然 401；而浏览器对任何非 2xx 的 fetch 都会自己往控制台打一行
      //   "GET ... 401 (Unauthorized)"，JS 侧屏蔽不掉。执行页是开机即挂载的
      //   （v-show 保活），不拦的话每个未登录访客一进来就带出两条 401。
      if (!useAuthStore().controlActive) { this.files = []; return; }
      try {
        const r = await apiControl("/control/files");
        if (r.status === 401) return;
        if (r.ok) { const d = await r.json(); this.files = d.items || []; }
      } catch (e) { /* 静默 */ }
    },
    async loadAll() {
      await Promise.all([this.loadPoints(), this.loadPrograms(), this.loadFiles()]);
    },

    // ================= 滑块示教 =================
    /**
     * ★ P0-1 修复：滑块/基准一变，缓存的 teachResult 立即作废。
     *   改造前 applyTeach 会直接复用上一次预演的 target —— 滑块改动晚于预演时，
     *   下发的就是**旧目标**（想走 A 结果走了 B）。现在任何改变目标的入口都先作废。
     */
    invalidateTeach() {
      this.teachResult = null;
      if (previewTimer) { clearTimeout(previewTimer); previewTimer = null; }
    },
    /** 用当前实时姿态初始化示教滑块。 */
    syncTeachFromRobot() {
      const robot = useRobotStore();
      // ★ 读 readoutQ（≡ 3D 画面姿态），不用 latestPose 遥测别名 —— 保证滑块基准与画面一致。
      const q = (robot.readoutQ && robot.readoutQ.length === 6)
        ? robot.readoutQ : [0, 0, 0, 0, 0, 0];
      for (let i = 0; i < 6; i++) this.teachQ[i] = Number(Number(q[i] || 0).toFixed(1));
      const t = robot.readoutTcp || { x: 0, y: 0, z: 0 };
      this.teachTcp.x = Number(Number(t.x || 0).toFixed(1));
      this.teachTcp.y = Number(Number(t.y || 0).toFixed(1));
      this.teachTcp.z = Number(Number(t.z || 0).toFixed(1));
      this.invalidateTeach();
    },
    onTeachSlider(i, e) {
      this.teachQ[i] = parseFloat(e.target.value) || 0;
      this.invalidateTeach();
      this.schedulePreview();
    },
    schedulePreview(delay = 220) {
      if (previewTimer) clearTimeout(previewTimer);
      previewTimer = setTimeout(() => { previewTimer = null; this.previewTeach(); }, delay);
    },
    /** 预演：把目标解算/校验一遍，并把残影摆到目标位上。只读，绝不下发。 */
    async previewTeach() {
      if (!useAuthStore().controlActive) return;
      this.teachBusy = true;
      try {
        const body = this.teachMode === "joint"
          ? { mode: "joint", joints: this.teachQ.map(Number), duration_ms: 1500, steps: 40 }
          : { mode: "cartesian",
              tcp: { x: +this.teachTcp.x, y: +this.teachTcp.y, z: +this.teachTcp.z },
              duration_ms: 1500, steps: 40 };
        const r = await apiControl("/control/preview", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (r.status === 401 || r.status === 403) { this.needAuth(); return; }
        const d = await r.json();
        this.teachResult = d;
        if (d.ok && d.target && this.teachGhost) setGhostPose(d.target);
        else showGhost(false);
      } catch (e) {
        this.teachResult = { ok: false, error: e.message };
      } finally { this.teachBusy = false; }
    },
    /** 直角坐标 → 关节角（IK），成功后回填关节滑块。 */
    async solveCartesian() {
      if (this.needAuth()) return;
      this.teachBusy = true;
      try {
        const r = await apiControl("/control/ik", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tcp: { x: +this.teachTcp.x, y: +this.teachTcp.y, z: +this.teachTcp.z },
            keep_orientation: true,
          }),
        });
        if (r.status === 401 || r.status === 403) { this.needAuth(); return; }
        const d = await r.json();
        if (d.ok && d.joints) {
          for (let i = 0; i < 6; i++) this.teachQ[i] = Number(Number(d.joints[i] || 0).toFixed(1));
          this.invalidateTeach();          // ★ 滑块被回填 → 旧预演作废
          await this.previewTeach();
        } else {
          this.teachResult = { ok: false, error: "目标不可达或超出关节限位" };
        }
      } catch (e) { this.teachResult = { ok: false, error: e.message }; }
      finally { this.teachBusy = false; }
    },
    /** 执行示教目标（按下发）。★ P0-1：无条件重算，绝不信任缓存的目标。 */
    async applyTeach() {
      if (this.needAuth()) return;
      this.teachBusy = true;
      try {
        // 无条件重新预演：滑块可能在最后一次预演之后又被动过。
        await this.previewTeach();
        if (!this.teachResult || !this.teachResult.ok || !this.teachResult.target) return;
        const target = this.teachResult.target;
        // 断言：joint 模式下预演目标必须与滑块一致（±0.05°），否则拒绝下发。
        if (this.teachMode === "joint") {
          let dev = 0;
          for (let i = 0; i < 6; i++) {
            dev = Math.max(dev, Math.abs((+target[i] || 0) - (+this.teachQ[i] || 0)));
          }
          if (dev > 0.05) {
            this.teachResult = { ok: false, error: "预演目标与滑块偏差 " + dev.toFixed(3) + "°，已拒绝下发" };
            this.logLine("err", "示教校验失败：目标与滑块偏差 " + dev.toFixed(3) + "°");
            return;
          }
        }
        // ★ 残影门控：按目标位姿量围栏/地面，危险或碰撞直接拒绝（不按实体机当前位姿拦）。
        const gate = this.gateAt(target);
        if (!gate.ok) { this.teachResult = { ok: false, error: gate.reason }; return; }
        const r = await apiControl("/control/move", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ joints: target, speed_pct: this.teachSpeed }),
        });
        const d = await r.json();
        this.execState = d;
        if (d.ok) {
          // ★ 姿态权威模型：不再直接驱动 3D —— 记录指令目标，
          //   真机由遥测跟上，模拟由"指令即位置"兜底（Stage C 后由仿真机走位）。
          useRobotStore().noteCommand(d.target.map(Number));
          this.logLine("ok", "示教到位：" + d.target.map((v) => (+v).toFixed(1)).join(", "));
        } else {
          this.logLine("err", "示教下发失败：" + (d.message || d.error || "未知"));
        }
      } catch (e) { this.execState = { ok: false, error: e.message }; }
      finally { this.teachBusy = false; }
    },

    // ================= 点动 =================
    stopTimers() {
      if (keepAliveTimer) { clearInterval(keepAliveTimer); keepAliveTimer = null; }
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
      pressInfo = null;
      this.holding = -1;
      this.holdingDir = 0;
    },
    async refreshJog() {
      try {
        const r = await apiControl("/control/jog");
        if (r.ok) this.jogState = await r.json();
      } catch (e) { /* 静默 */ }
    },
    /** 按住 → 连续点动；周期性 keepalive（死人开关）+ 轮询目标姿态驱动 3D。 */
    async startJog(joint, dir) {
      if (this.needAuth()) return;
      if (this.UNSAFE) return;
      this.stopTimers();
      this.jogBusy = true;
      try {
        const r = await apiControl("/control/jog/start", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ joint, dir, speed_dps: this.jogSpeed }),
        });
        if (r.status === 401 || r.status === 403) { this.needAuth(); return; }
        const d = await r.json();
        if (!r.ok || !d.ok) {
          const msg = d.message || d.error || "点动被拒绝";
          this.execState = { ok: false, error: msg };
          this.logLine("err", `连续点动 J${joint} 被拒绝：${msg}`);
          return;
        }
        this.holding = joint;
        this.holdingDir = dir;
        this.logLine("info", `连续点动 J${joint} ${dir > 0 ? "正向" : "反向"} @${this.jogSpeed}°/s`);
        // 死人开关：0.4s 一次使能保持，1.5s 收不到后端自动停
        keepAliveTimer = setInterval(async () => {
          try { await apiControl("/control/jog/keepalive", { method: "POST" }); }
          catch (e) { /* 静默 */ }
        }, 400);
        pollTimer = setInterval(async () => {
          try {
            const rr = await apiControl("/control/jog");
            if (!rr.ok) return;
            const s = await rr.json();
            this.jogState = s;
            // ★ 不再直接驱动 3D：把点动引擎的当前目标记入指令通道，
            //   由 App.vue 渲染循环统一显示；真机模式下遥测会跟上真实位置。
            if (s && s.active && s.target) useRobotStore().noteCommand(s.target.map(Number));
            if (s && !s.active) { this.stopTimers(); this.refreshSessionPose(); }
          } catch (e) { /* 静默 */ }
        }, 180);
      } catch (e) { this.execState = { ok: false, error: e.message }; }
      finally { this.jogBusy = false; }
    },
    async endJog() {
      if (this.holding < 0) return;
      this.stopTimers();
      try {
        const r = await apiControl("/control/jog/stop", { method: "POST" });
        if (r.ok) this.jogState = await r.json();
      } catch (e) { /* 静默 */ }
      this.refreshSessionPose();
    },
    /** 增量点动：按一次走固定角度。 */
    async stepJog(joint, dir) {
      if (this.needAuth()) return;
      if (this.UNSAFE) return;
      await this.endJog();
      this.jogBusy = true;
      try {
        const r = await apiControl("/control/jog/step", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ joint, dir, angle_deg: this.jogStepDeg,
                                 speed_dps: this.jogSpeed }),
        });
        if (r.status === 401 || r.status === 403) { this.needAuth(); return; }
        const d = await r.json();
        if (!r.ok || !d.ok) {
          const msg = d.message || d.error || "增量点动被拒绝";
          this.execState = { ok: false, error: msg };
          this.logLine("err", `增量点动 J${joint} 被拒绝：${msg}`);
          return;
        }
        this.execState = { ok: true, target: d.target, mode: d.mode };
        useRobotStore().noteCommand(d.target.map(Number));
        if (d.warnings && d.warnings.length) this.execState.warning = d.warnings[0];
        this.syncTeachFromRobot();
      } catch (e) { this.execState = { ok: false, error: e.message }; }
      finally { this.jogBusy = false; }
    },
    // 点动按钮的按压模型：★ 不能同时用 @pointerdown + @click ——
    //   长按会先 start(连续)，松手又触发一次 click(步进)，两个动作打架。
    //   这里统一到 pointer 事件：按住 >260ms 视为连续点动，短按视为走一格。
    jogPress(joint, dir) {
      if (this.needAuth()) return;
      if (this.UNSAFE) return;
      pressInfo = { joint, dir };
      if (pressTimer) clearTimeout(pressTimer);
      pressTimer = setTimeout(() => {
        pressTimer = null;
        if (pressInfo) this.startJog(pressInfo.joint, pressInfo.dir);
      }, HOLD_MS);
    },
    jogRelease() {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
      const info = pressInfo;
      pressInfo = null;
      if (this.holding >= 0) { this.endJog(); return; }   // 连续点动中 → 停
      if (info) this.stepJog(info.joint, info.dir);       // 短按 → 走一格
    },
    // ================= 残影预演 =================
    /** 把残影摆到某个点位（按 point_id 在已加载点位里找关节角）。 */
    previewPointGhost(pointId) {
      const p = this.points.find((x) => x.id === pointId);
      if (p && p.joints && p.joints.length === 6) setGhostPose(p.joints);
      else showGhost(false);
    },
    /** 在点位列表上悬停 → 残影预演该点位。 */
    hoverPoint(p) {
      if (p && p.joints && p.joints.length === 6) setGhostPose(p.joints);
    },
    unhoverPoint() { if (!this.runBusy) showGhost(false); },
    hideGhost() { showGhost(false); highlightJoint(-1); },

    /**
     * **下发门控**：把残影摆到目标位姿，立刻量一次围栏/地面 —— 不撞才放行。
     *
     * ★ 这是"点位执行不用拦截，只要残影预演没有碰撞/不变红就能动"的落点：
     *   - 判据来自**残影的目标位姿**，不是实体机当前位姿；
     *   - 同步执行（不依赖渲染循环那一帧），所以"没悬停过就直接点执行"也拦得住；
     *   - 侧栏围栏面板与残影同源变色，拒绝时用户看到的就是那片红。
     *
     * @param {number[]} qDeg 目标关节角(度)
     * @returns {{ok:boolean, state?:string, reason?:string}}
     */
    gateAt(qDeg) {
      if (!qDeg || qDeg.length !== 6) return { ok: true };   // 没有目标可预演 → 不拦
      setGhostPose(qDeg);
      const r = evalSafety();
      if (!r || STATE_RANK[r.state] < STATE_RANK.danger) {
        return { ok: true, state: r ? r.state : "safe" };
      }
      const why = r.state === "hit" ? "目标位姿会碰撞" : "目标位姿危险";
      const where = r.zoneName ? `（${r.zoneName}）` : "";
      const reason = `${why}${where}，已拒绝下发`;
      this.logLine("err", "残影预演拦截：" + reason);
      return { ok: false, state: r.state, reason };
    },

    // ================= 点位 CRUD 辅助 =================
    /** 把示教位姿填进"新建点位"表单（返回填充好的 payload，表单由页面持有）。 */
    teachAsPointPayload() {
      return {
        kind: "joint",
        joints: this.teachQ.map(Number),
        name: "示教点 " + new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      };
    },
    async savePoint(payload, id) {
      const isNew = !id;
      const opts = { headers: { "Content-Type": "application/json" },
                     body: JSON.stringify(payload) };
      const r = isNew
        ? await apiControl("/points", { ...opts, method: "POST" })
        : await apiControl(`/points/${id}`, { ...opts, method: "PUT" });
      if (r.ok) { await this.loadPoints(); return { ok: true }; }
      const j = await r.json().catch(() => ({}));
      if (r.status === 401 || r.status === 403) {
        this.needAuth();
        return { ok: false, error: r.status === 403 ? "该操作需要管理员权限" : "需要控制权限才能管理点位" };
      }
      return { ok: false, error: j.message || j.detail || ("保存失败：" + r.status) };
    },
    async delPoint(p) {
      const r = await apiControl(`/points/${p.id}`, { method: "DELETE" });
      if (r.ok) { await this.loadPoints(); return true; }
      if (r.status === 401 || r.status === 403) this.needAuth();
      return false;
    },
    /** 直角坐标点位保存前先解算成关节角（后端只吃 joints）。 */
    async solveTcp(tcp) {
      if (this.needAuth()) return { ok: false, error: "直角坐标解析需先获取控制权限" };
      const r = await apiControl("/control/ik", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tcp, keep_orientation: true }),
      });
      if (!r.ok) return { ok: false, error: "IK 解析失败" };
      const d = await r.json();
      if (!d.ok) return { ok: false, error: "目标不可达或超限位" };
      return { ok: true, joints: d.joints };
    },

    // ================= 执行：单点 / 程序 / 按文件名 =================
    /** 指令成功后的显示钩子：只记录指令目标，绝不直接驱动 3D（App 循环统一写）。 */
    animateTo(target) {
      if (!target) return;
      useRobotStore().noteCommand(target.map((v) => +v));
    },
    async execPoint(p) {
      if (this.needAuth()) return;
      // ★ 残影门控：先把残影摆到该点位量一次，不撞才发（替代原先"按实体机位姿拦"）。
      const gate = this.gateAt(p && p.joints);
      if (!gate.ok) { this.execState = { ok: false, error: gate.reason }; return; }
      this.runBusy = true;
      this.execState = null;
      try {
        this.hideGhost();
        const r = await apiControl("/control/move", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ point_id: p.id, speed_pct: this.speed }),
        });
        const d = await r.json();
        this.execState = d;
        if (d.ok) {
          this.animateTo(d.target, d.mode);
          this.logLine("ok", `点位「${p.name}」已下发 @${this.speed}%`);
        } else {
          this.logLine("err", `点位「${p.name}」下发失败：${d.message || d.error || ""}`);
        }
      } catch (e) {
        this.execState = { ok: false, error: e.message };
        this.logLine("err", "点位下发异常：" + e.message);
      } finally { this.runBusy = false; }
    },
    /** 运行"点位序列"程序：逐步下发 + 残影预演下一步 + 逐步日志 + 可中止。 */
    async execProgram(pr) {
      if (this.needAuth()) return;
      const items = pr.items || [];
      if (!items.length) {
        this.logLine("warn", `程序「${pr.name}」没有任何步骤`);
        return;
      }
      this.runBusy = true;
      this.running = true;
      this.runAbort = false;
      this.runKind = "program";
      this.runName = pr.name;
      this.lastRunTarget = pr;              // 记住目标：空格键要能重复运行它
      this.runTotal = items.length;
      this.runStep = 0;
      this.runDry = false;
      this.logLine("info", `开始运行程序「${pr.name}」（${items.length} 步）`);
      const t0 = Date.now();
      let passed = 0;
      let failed = null;
      try {
        for (let i = 0; i < items.length; i++) {
          if (this.runAbort) { this.logLine("warn", "已被用户中止"); break; }
          const it = items[i];
          this.runStep = i + 1;
          // ★ 逐步门控：每一步都先按"这一步的目标位姿"量一次残影，不撞才发。
          //   某一步过不去 → 就地中断，绝不"带病"往下跑。
          const pj = (this.points.find((x) => x.id === it.point_id) || {}).joints;
          const gate = this.gateAt(pj);
          if (!gate.ok) {
            failed = `第 ${i + 1} 步${gate.reason}`;
            this.execState = { ok: false, error: gate.reason };
            this.logLine("err", failed);
            break;
          }
          const nxt = items[i + 1];
          if (nxt) this.previewPointGhost(nxt.point_id);
          else showGhost(false);
          const r = await apiControl("/control/move", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ point_id: it.point_id, speed_pct: this.speed }),
          });
          const d = await r.json();
          if (d.ok) {
            passed++;
            this.animateTo(d.target, d.mode);
            this.logLine("ok", `第 ${i + 1}/${items.length} 步完成`
              + (it.dwell_ms ? `（停留 ${it.dwell_ms}ms）` : ""));
            await sleep((it.dwell_ms || 0) + 400);
          } else {
            failed = d.message || d.error || `第 ${i + 1} 步失败`;
            this.execState = d;
            this.logLine("err", failed);
            break;
          }
        }
      } catch (e) {
        failed = e.message;
        this.logLine("err", "运行异常：" + e.message);
      } finally {
        this.running = false;
        this.runBusy = false;
        showGhost(false);
        const ms = Date.now() - t0;
        const okAll = !failed && passed === items.length;
        this.lastRun = {
          name: pr.name, kind: "program", ok: okAll, dryRun: false,
          passed, count: items.length, duration_ms: ms, ts: Date.now(), error: failed,
        };
        this.logLine(okAll ? "ok" : "err",
          okAll ? `程序「${pr.name}」运行完成：${passed}/${items.length} 步，${(ms / 1000).toFixed(1)}s`
                : `程序「${pr.name}」中断：${passed}/${items.length} 步`);
      }
    },
    /**
     * 「测试跑」：对某个可执行目标做一次**空跑校验**（dry_run=true）。
     *
     * ★ 与「执行」的硬区别：永远不下发、不加载、不运行控制器程序、不移动机器人 ——
     *   只把目标解析一遍，对每个移动步做 IK / 限位 / 可达预演，io 步只计步。
     *   用来回答"这个程序能不能跑、跑得对不对"，不产生任何真实运动。
     *   复用同一套 runFile 通道（结果落在 fileResult / lastRun / 日志），
     *   所以示教器程序、本地点位序列、json 都能用它验。
     */
    async testRun(name) {
      if (this.needAuth()) return;
      const n = (name || "").trim();
      if (!n) return;
      this.fileName = n;
      this.dryRun = true;
      await this.runFile();
    },

    /** 按文件名执行：程序 / 点位 / programs 目录 JSON；dryRun 只校验不下发。 */
    async runFile() {
      if (this.needAuth()) return;
      if (!this.fileName.trim()) {
        this.fileResult = { ok: false, error: "请先选择或输入文件名" };
        return;
      }
      // ★ 只按残影预演拦：按文件名执行事先不知道目标序列，前端拦不了"要去哪"，
      //   真正的兜底在后端 guard_check（它看的是前端上报的同一份围栏状态）。
      if (this.ghostUnsafe && !this.dryRun) return;
      this.fileBusy = true;
      this.runBusy = true;
      this.runKind = "file";
      this.runName = this.fileName.trim();
      this.runTotal = 0;
      this.runStep = 0;
      this.runDry = this.dryRun;
      this.running = !this.dryRun;
      this.logLine("info", this.dryRun
        ? `试运行「${this.runName}」（只校验，绝不下发）`
        : `执行「${this.runName}」@${this.speed}%`);
      const t0 = Date.now();
      try {
        const r = await apiControl("/control/run-file", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filename: this.runName, dry_run: this.dryRun,
            speed_pct: this.speed, steps: 40,
          }),
        });
        if (r.status === 401 || r.status === 403) { this.needAuth(); return; }
        const d = await r.json();
        this.fileResult = d;
        // FastAPI 的 HTTPException(404, detail={...}) 会把 detail 包进响应体的
        // "detail" 字段；message/candidates 实际在 d.detail 内层。这里先解一层，
        // 否则 name/passed/count/candidates 全是 undefined（见"undefined/undefined 步"）。
        const body = (d && typeof d === "object" && d.detail !== undefined
                      && typeof d.detail === "object" && d.detail !== null)
          ? d.detail : d;
        const candidates = (body && body.candidates) || d.candidates;
        if (!r.ok) {
          const msg = (body && (body.message || body.error || body.detail))
            || (typeof d.detail === "string" ? d.detail : "")
            || ("后端返回 " + r.status);
          this.fileResult = { ok: false, error: msg, candidates };
          this.logLine("err", `按文件名执行失败：${msg}`);
          if (candidates && candidates.length) {
            this.logLine("info", `可执行目标 ${candidates.length} 个：`
              + candidates.map((c) => c && (c.name || c.id)).join("、"));
          }
        } else {
          this.runTotal = d.count || 0;
          this.runStep = this.runTotal;
          const name = (d && (d.name || (d.target && d.target.name))) || this.runName;
          const stepTxt = (d && Number.isFinite(d.count)) ? `（${d.passed || 0}/${d.count} 步）` : "";
          this.logLine(d.ok ? "ok" : "err",
            (d.dry_run ? "试运行" : "执行") + (d.ok ? "通过" : "发现问题") + `：${name}${stepTxt}`);
          if (!d.ok && d.message) this.logLine("err", String(d.message));
        }
        if (d.ok && !d.dry_run && d.steps && d.steps.length) {
          const last = [...d.steps].reverse().find((s) => s.target);
          if (last) useRobotStore().noteCommand(last.target.map(Number));
        }
        this.lastRun = {
          name: (d && d.name) || this.runName, kind: "file",
          ok: !!(d && d.ok), dryRun: !!this.dryRun,
          passed: (d && d.passed) || 0, count: (d && d.count) || 0,
          duration_ms: (d && d.duration_ms) || (Date.now() - t0),
          ts: Date.now(), error: d && d.error,
        };
      } catch (e) {
        this.fileResult = { ok: false, error: e.message };
        this.logLine("err", "执行异常：" + e.message);
      } finally {
        this.fileBusy = false;
        this.runBusy = false;
        this.running = false;
      }
    },
    abortRun() {
      this.runAbort = true;
      this.running = false;
      this.logLine("warn", "已请求中止（当前步结束后停止）");
    },

    /**
     * 空格键的落点：运行中 → 中止；空闲 → 重复运行上一次的点位序列程序。
     *
     * ★ 三条自我约束（对应现场最容易出的三种误操作）：
     *   1) 空闲时**绝不**自己挑程序：没有 lastRunTarget 就只记一行日志，不做任何下发；
     *   2) 运行中只调用 abortRun()（软中止，当前步走完再停），不重复下发；
     *   3) 文件程序（runFile）不受空格控制 —— 它的运行态与点位序列不同，
     *      乱接会让"空格停下来的其实是另一条通道"。这里显式挡掉并给出提示。
     */
    runOrAbort() {
      if (this.running) {
        if (this.runKind !== "program") {
          this.logLine("warn", "当前运行的是文件程序，请在「程序执行」页用按钮停止");
          return;
        }
        this.abortRun();
        return;
      }
      if (!this.lastRunTarget) {
        this.logLine("info", "空格 = 运行/停止程序；请先在「程序执行」页选一个程序跑一次");
        return;
      }
      this.execProgram(this.lastRunTarget);
    },

    // ================= 急停 / 引擎状态 =================
    async doEstop() {
      if (this.needAuth()) return;
      this.estopBusy = true;
      try {
        this.stopTimers();                 // ★ 点动是独立线程，先停它再急停
        this.runAbort = true;
        const r = await apiControl("/control/estop", { method: "POST" });
        if (r.ok) {
          this.estopState = await r.json();
          this.logLine("err", "已下发急停");
        }
        await this.refreshJog();
      } finally { this.estopBusy = false; }
    },
    async resetEstop() {
      if (this.needAuth()) return;
      const r = await apiControl("/control/estop/reset", { method: "POST" });
      if (r.ok) {
        this.estopState = await r.json();
        this.logLine("ok", "急停已复位");
      }
    },
    async refreshState() {
      if (!useAuthStore().controlActive) { this.estopState = null; return; }
      const r = await apiControl("/control/state");
      if (r.ok) this.estopState = await r.json();
      await this.refreshJog();
    },

    // ================= 会话收尾 =================
    /** 用后端最新目标刷新本地姿态基准。 */
    refreshSessionPose() {
      setTimeout(() => this.syncTeachFromRobot(), 120);
    },
    /** 离开执行类视图：停点动、收残影。
     *  ★ 姿态权威模型下不再有"交还姿态控制权"这回事 —— 遥测永远不被抑制，
     *    demo 只有 RealMonitor 能设，exec 无权也不需要碰它。 */
    leaveView() {
      this.stopTimers();
      showGhost(false);
      highlightJoint(-1);
    },
    dispose() {
      this.stopTimers();
      if (previewTimer) { clearTimeout(previewTimer); previewTimer = null; }
      this.leaveView();
    },
  },
});
