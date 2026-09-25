// =====================================================================
// 机器人参数卡回归（headless，纯函数 + 源码静态断言，无需浏览器）
//
// 背景：连接状态 / 关节角 / 末端 TCP 这三块**读数**仍由
//   components/RobotParamsCard.vue 一处实现，但现场重构后只有「真实监控」页
//   引用（:connection="false"，只显示关节角+TCP）。连接状态（含重连）已整合
//   到底栏「机器人链路」灯；点位执行/程序执行两页不再挂任何读数卡
//   （点位只留滑块示教，程序只留就绪提示盒）。
//
// 这里钉死两类最容易回归的问题：
//   ① 量程归一化的**边界**（限位缺失 / span=0 / 读数是 NaN / 角度越界）——
//      算错时页面只是"条不太对"，没人会去查，纯靠断言守。
//   ② **实现漂移**：某个页面又自己写了一份读数（第二份 barPct / 第二份限位数组），
//      于是各页的取色、量程、悬停行为悄悄不一致 —— 现场会怀疑"哪个是真的"。
//
// 运行： node tools/verify_robot_params.mjs
// =====================================================================
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { sanitizeLimits, barPct, barWidthStyle } from "../src/utils/jointScale.js";
import { DEFAULT_LIMITS } from "../src/stores/robot.js";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
let pass = 0, fail = 0;
function ok(name, cond, extra = "") {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (extra ? "   → " + extra : "")); }
}
const read = (p) => fs.readFileSync(path.join(ROOT, p), "utf8");
const scriptOf = (src) => {
  const m = src.match(/<script setup>([\s\S]*?)<\/script>/);
  return m ? m[1] : "";
};
const templateOf = (src) => {
  const m = src.match(/<template>([\s\S]*?)<\/template>/);
  // ★ 用贪婪到最后一个 </template>：模板里有 <template #hud> 之类的插槽标签
  return m ? src.slice(src.indexOf("<template>")) : "";
};

console.log("== 机器人参数卡回归 ==");

// ---------- A. 限位合法性 ----------
console.log("\n[A] sanitizeLimits：坏限位必须被判掉（返回 null），不许拿坏值去算条宽");
ok("出厂限位通过", null !== sanitizeLimits(DEFAULT_LIMITS));
ok("正常 6 轴通过", null !== sanitizeLimits(
  [{ min: -1, max: 1 }, { min: -1, max: 1 }, { min: -1, max: 1 },
   { min: -1, max: 1 }, { min: -1, max: 1 }, { min: -1, max: 1 }]));
ok("null → null", sanitizeLimits(null) === null);
ok("undefined → null", sanitizeLimits(undefined) === null);
ok("空数组 → null", sanitizeLimits([]) === null);
ok("对象（非数组）→ null", sanitizeLimits({ min: -1, max: 1 }) === null);
ok("只有 5 轴 → null", sanitizeLimits(DEFAULT_LIMITS.slice(0, 5)) === null);
ok("只有 7 轴 → null", sanitizeLimits([...DEFAULT_LIMITS, { min: 0, max: 1 }]) === null);
ok("min == max（span=0）→ null", sanitizeLimits(
  [{ min: 5, max: 5 }, { min: -1, max: 1 }, { min: -1, max: 1 },
   { min: -1, max: 1 }, { min: -1, max: 1 }, { min: -1, max: 1 }]) === null);
ok("min > max（反了）→ null", sanitizeLimits(
  [{ min: 5, max: -5 }, { min: -1, max: 1 }, { min: -1, max: 1 },
   { min: -1, max: 1 }, { min: -1, max: 1 }, { min: -1, max: 1 }]) === null);
ok("min 是 NaN → null", sanitizeLimits(
  [{ min: NaN, max: 1 }, { min: -1, max: 1 }, { min: -1, max: 1 },
   { min: -1, max: 1 }, { min: -1, max: 1 }, { min: -1, max: 1 }]) === null);
ok("项为 null → null", sanitizeLimits(
  [null, { min: -1, max: 1 }, { min: -1, max: 1 },
   { min: -1, max: 1 }, { min: -1, max: 1 }, { min: -1, max: 1 }]) === null);
ok("字符串数字可接受（后端 JSON 偶尔给字符串）", null !== sanitizeLimits(
  [{ min: "-170", max: "170" }, { min: -170, max: 90 }, { min: -85, max: 150 },
   { min: -180, max: 180 }, { min: -115, max: 115 }, { min: -360, max: 360 }]));
ok("sanitize 后的 min/max 都是真数字", (() => {
  const s = sanitizeLimits([{ min: "-170", max: "170" }, { min: -1, max: 1 }, { min: -1, max: 1 },
    { min: -1, max: 1 }, { min: -1, max: 1 }, { min: -1, max: 1 }]);
  return typeof s[0].min === "number" && s[0].min === -170;
})());

// ---------- B. 条宽边界 ----------
console.log("\n[B] barPct：恒在 [0,100]、绝不 NaN、越界钳位");
const L = { min: -180, max: 180 };
const L6 = { min: -360, max: 360 };
ok("中点 = 50", barPct(0, L) === 50);
ok("下界 = 0", barPct(-180, L) === 0);
ok("上界 = 100", barPct(180, L) === 100);
ok("超出上界 → 钳到 100（不溢出）", barPct(9999, L) === 100);
ok("超出下界 → 钳到 0", barPct(-9999, L) === 0);
ok("非对称量程：J2(-170..90) 的 0 点不在半宽处",
  Math.abs(barPct(0, { min: -170, max: 90 }) - (170 / 260) * 100) < 1e-9,
  "得到 " + barPct(0, { min: -170, max: 90 }));
ok("J6 量程 ±360 可用", barPct(124.3, L6) > 60 && barPct(124.3, L6) < 70);
ok("lim 为 null → 0", barPct(10, null) === 0);
ok("lim 为 undefined → 0", barPct(10, undefined) === 0);
ok("span=0 → 0（不是除零爆炸）", barPct(5, { min: 5, max: 5 }) === 0);
ok("span 为负 → 0", barPct(5, { min: 10, max: -10 }) === 0);

const BADVALS = [undefined, null, NaN, Infinity, -Infinity, "", "abc", {}, []];
let badOut = [];
for (const v of BADVALS) {
  const r = barPct(v, L);
  if (!Number.isFinite(r) || r < 0 || r > 100) badOut.push(JSON.stringify(v) + "→" + r);
}
ok("8 种坏读数全部落在 [0,100] 且非 NaN", badOut.length === 0, badOut.join(" | "));
ok("坏读数按 0 处理（等价于 angle=0 的位置）", barPct(NaN, L) === barPct(0, L));
ok("NaN 不会产出 NaN%", barWidthStyle(NaN, L).width === "50.0%", barWidthStyle(NaN, L).width);

// 全量扫描：随机值也不许出界
let oob = 0;
for (let k = 0; k < 400; k++) {
  const v = (Math.random() - 0.5) * 4000;
  const lim = { min: -180 + Math.random() * 10, max: 180 - Math.random() * 10 };
  const r = barPct(v, lim);
  if (!Number.isFinite(r) || r < 0 || r > 100) oob++;
}
ok("400 组随机值 + 随机量程全部在界内", oob === 0, "越界 " + oob + " 次");
ok("barWidthStyle 产出可用的 CSS 宽度串", /^\d+(\.\d)?%$/.test(barWidthStyle(0, L).width),
  barWidthStyle(0, L).width);

// ---------- C. 读数卡只有「真实监控」页引用 ----------
console.log("\n[C] 读数卡只有「真实监控」页引用；点位/程序页一律不挂");
const ALL_VIEWS = {
  "真实监控": "src/components/RealMonitor.vue",
  "点位执行": "src/components/PointExecView.vue",
  "程序执行": "src/components/ProgramExecView.vue",
};
{
  const src = read(ALL_VIEWS["真实监控"]);
  ok("真实监控：import 了 RobotParamsCard",
    /import\s+RobotParamsCard\s+from\s+["'][^"']*RobotParamsCard\.vue["']/.test(src));
  ok("真实监控：模板里真的渲染了 <RobotParamsCard",
    /<RobotParamsCard\b/.test(templateOf(src)));
}
for (const [label, p] of [["点位执行", ALL_VIEWS["点位执行"]], ["程序执行", ALL_VIEWS["程序执行"]]]) {
  const src = read(p);
  ok(label + "：不再 import RobotParamsCard（读数已交真实监控或移除）",
    !/import\s+RobotParamsCard\s+from\s+["'][^"']*RobotParamsCard\.vue["']/.test(src));
  ok(label + "：模板里不再渲染 <RobotParamsCard",
    !/<RobotParamsCard\b/.test(templateOf(src)));
}

const card = read("src/components/RobotParamsCard.vue");
const cardTpl = templateOf(card);
const cardJs = scriptOf(card);

// ---------- D. 组件内部：三个块的门控与 props 同名 ----------
console.log("\n[D] RobotParamsCard：三个块的门控与 props 对应");
for (const [blk, prop] of [["连接状态", "connection"], ["关节角", "joints"], ["末端 TCP", "tcp"]]) {
  ok("「" + prop + "」块存在且由同名 prop 门控",
    new RegExp('v-if="' + prop + '"').test(cardTpl) && cardTpl.includes(blk),
    "缺 v-if=\"" + prop + "\" 或块标题「" + blk + "」");
  ok("props 声明里有 " + prop,
    new RegExp("\\b" + prop + ":\\s*\\{\\s*type:\\s*Boolean").test(cardJs));
}
ok("props 声明里有 pose（数组）", /pose:\s*\{\s*type:\s*Array/.test(cardJs));
ok("props 声明里有 reconnect（布尔）", /reconnect:\s*\{\s*type:\s*Boolean/.test(cardJs));
ok("定义了 reconnected 事件", /defineEmits\(\s*\[[^\]]*["']reconnected["']/.test(cardJs));

// ---------- E. 位姿来源的兜底顺序 ----------
console.log("\n[E] 位姿来源：显式 prop 优先于 store 的读数姿态（readoutQ ≡ 3D 画面）");
ok("q = props.pose || robot.readoutQ || 全零 的顺序正确",
  /props\.pose\s*\|\|\s*robot\.readoutQ\s*\|\|/.test(cardJs.replace(/\s+/g, " ")),
  "实际：" + (cardJs.match(/const\s+q\s*=\s*computed\([^)]*\)/) || ["未找到"])[0]);
ok("卡片不再读旧别名 latestPose（阶段 7 已迁到 readoutQ）",
  !/robot\.latestPose/.test(cardJs));
ok("limits 兜底用 store 的 DEFAULT_LIMITS，不是就地手写第二份限位数组",
  /sanitizeLimits\(robot\.limits\)\s*\|\|\s*DEFAULT_LIMITS/.test(cardJs.replace(/\s+/g, " ")));
ok("组件里没有再手写 min/max 字面量数组（-170 / 90 / 150 之类）",
  !/\{\s*min:\s*-170/.test(cardJs), "发现手写的限位字面量");

// ---------- F. 漂移守卫：别处不许再实现一份读数 ----------
console.log("\n[F] 漂移守卫：读数的实现只许存在于 RobotParamsCard");
// ★ 判据要**结构化**，别按标题文字找：RealMonitor 的注释里就写着「关节角 + 末端 TCP」，
//   按文字找会把注释当实现（假 FAIL）。这里按"是不是真的在渲染 tcp 读数"来判。
for (const [label, p] of Object.entries(ALL_VIEWS)) {
  const src = read(p);
  const tpl = templateOf(src);
  ok(label + "：没有第二份「关节角 (deg)」读数块", !/关节角 \(deg\)/.test(tpl));
  ok(label + "：没有第二份条宽实现（bar-fill / barPct）",
    !/bar-fill|barPct\s*\(/.test(src));
  ok(label + "：没有第二份限位字面量数组", !/\{\s*min:\s*-170/.test(src));
  ok(label + "：没有第二份 TCP 读数（直接渲染 robot.tcp.x/y/z）",
    !/robot\.tcp\.(x|y|z)\.toFixed/.test(tpl),
    "仍在模板里自己渲染 TCP 读数");
  // ★ 判据用「读数专有的 class / 函数名」，不要用 v-for 形状：
  //   示教滑块也写 v-for="(n, i) in robot.axes"，按形状判会把滑块误伤成"第二份读数"。
  ok(label + "：没有第二份读数标记（rp-axis / rp-bar / rp-deg / rp-fill）",
    !/\brp-(axis|bar|deg|fill)\b/.test(src));
  ok(label + "：没有调用条宽纯函数（barWidthStyle / barPct）",
    !/\b(barWidthStyle|barPct)\s*\(/.test(src));
}

// ---------- G. 只读守卫 ----------
console.log("\n[G] 只读守卫：卡片里不许出现任何「改机器人」的写操作");
// ★ 判据更新（审计 P1-B2）：/reconnect 后端已补 require_control，卡片必须改走
//   带令牌的 apiControl()，否则按钮会静默 401。所以 apiControl 不能再一概算
//   违规 —— 真正要防的是「改姿态/点动/执行」这一类调用。
const FORBIDDEN = ["applyRobotPose", "setSimQ", "solveTcp", "execPoint", "execProgram",
                   "jogPress", "jogStart", "applyTeach"];
const hits = FORBIDDEN.filter((f) => cardJs.includes(f) || cardTpl.includes(f));
ok("没有姿态/点动/执行类写操作", hits.length === 0, "发现：" + hits.join(", "));
const urls = [...cardJs.matchAll(/(?:apiUrl|apiControl)\(\s*["']([^"']+)["']/g)].map((m) => m[1]);
ok("卡片里只有一个后端写请求，且就是 /reconnect", urls.length === 1 && urls[0] === "/reconnect",
  "找到：" + JSON.stringify(urls));
ok("重连按钮仅在 reconnect prop 为真时渲染", /v-if="reconnect"/.test(cardTpl));
ok("重连成功后 emit reconnected（让调用方去重算自己的状态机）",
  /emit\(\s*["']reconnected["']\s*\)/.test(cardJs));
ok("真实监控页不再传 reconnect（重连已整合到底栏「机器人链路」灯）",
  !/:reconnect=/.test(templateOf(read("src/components/RealMonitor.vue"))));
ok("点位执行页不传 reconnect", !/:reconnect=/.test(templateOf(read("src/components/PointExecView.vue"))));
ok("程序执行页不传 reconnect", !/:reconnect=/.test(templateOf(read("src/components/ProgramExecView.vue"))));

// ---------- H. 悬停高亮收尾 ----------
console.log("\n[H] 悬停高亮：必须同时改本地态与 3D；卸载必须取消");
ok("悬停时同时设置 hovered 与调用 highlightJoint(i)",
  /hoverJoint\(i\)\s*\{[^}]*hovered\.value\s*=\s*i[^}]*highlightJoint\(i\)/s.test(cardJs));
ok("移出时取消高亮", /unhoverJoint\(\)\s*\{[^}]*highlightJoint\(-1\)/s.test(cardJs));
ok("卸载时取消 3D 高亮（否则切页后关节一直亮着）",
  /onBeforeUnmount\([^)]*=>\s*highlightJoint\(-1\)\)/.test(cardJs.replace(/\s+/g, " "))
  || /onBeforeUnmount\([\s\S]*?highlightJoint\(-1\)/.test(cardJs));
// 阶段 7 后 RealMonitor 不再传 :pose（跳舞姿态经 store 的 demo 通道进入 readoutQ），
// 三页读数与 3D 全部同源于 displayQ —— 断言 RealMonitor 无残留的 :pose 绑定。
ok("真实监控页不再传 :pose（读数与 3D 同源，走 readoutQ）",
  !/<RobotParamsCard[^>]*:pose=/s.test(templateOf(read("src/components/RealMonitor.vue"))));

// ---------- I. 遮蔽守卫：setup 绑定不得与 prop 同名 ----------
// ★ 这是一次真实事故：组件里 `const tcp = computed(...)`（TCP 读数）与 prop `tcp`
//   （布尔门控）同名 → 模板里的 `v-if="tcp"` 绑到了**读数对象**（恒为真），
//   `:tcp="false"` 彻底失效 —— 于是「真实监控」页同时冒出两个「末端 TCP (mm)」。
//   这类遮蔽编译器不报错、页面也不报错，只是门控静默失灵，必须静态钉死。
console.log("\n[I] 遮蔽守卫：setup 绑定（const/let/function）不得与 prop 同名");
function shadowedProps(src) {
  const js = scriptOf(src);
  const m = js.match(/defineProps\(\{([\s\S]*?)\n\}\);/);
  if (!m) return [];
  const props = [...m[1].matchAll(/(\w+)\s*:\s*\{/g)].map((x) => x[1]);
  const binds = [...js.matchAll(/(?:const|let|function)\s+(\w+)/g)].map((x) => x[1]);
  return props.filter((p) => binds.includes(p));
}
ok("RobotParamsCard：没有 setup 绑定遮蔽 prop（tcp / joints / connection / pose / reconnect）",
  shadowedProps(card).length === 0, "被遮蔽：" + shadowedProps(card).join(", "));
ok("TCP 读数变量不叫 tcp（避开 prop.tcp）", !/\bconst\s+tcp\s*=/.test(cardJs));
ok("模板里的 TCP 读数用的是改名后的变量 tcpReadout",
  /tcpReadout\.x\.toFixed/.test(cardTpl));
const COMPONENTS = fs.readdirSync(path.join(ROOT, "src", "components")).filter((f) => f.endsWith(".vue"));
const shadowFiles = [];
for (const f of COMPONENTS) {
  const bad = shadowedProps(read(path.join("src", "components", f)));
  if (bad.length) shadowFiles.push(f + " → " + bad.join("/"));
}
ok("全量组件扫描：无任何 prop 被 setup 绑定遮蔽", shadowFiles.length === 0, shadowFiles.join(" | "));

// ---------- J. 真实监控页：读数卡只显示关节角 + TCP ----------
console.log("\n[J] 真实监控页：读数卡只显示关节角+TCP（连接块已移到底栏链路灯）");
const liveTpl = templateOf(read("src/components/RealMonitor.vue"));
const liveCards = [...liveTpl.matchAll(/<RobotParamsCard\b[^>]*\/>/gs)].map((m) => m[0]);
ok("真实监控页只引用一次 RobotParamsCard（读数块）", liveCards.length === 1,
  "实际 " + liveCards.length + " 次");
ok("读数卡关掉了 connection（连接状态只出现在底栏链路灯）",
  /:connection="false"/.test(liveCards[0] || ""), "卡片：" + (liveCards[0] || "未找到"));

console.log("");
console.log("结果: " + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
