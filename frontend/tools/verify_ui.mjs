/**
 * headless 回归：右侧栏宽度（`src/stores/ui.js`）
 *
 * 为什么值得单独测：宽度是**持久化**的，所以"改默认值"这件事有两种失败方式，
 * 而且都很难发现：
 *   1. 只改常量、不做迁移 → 老用户本地存着旧值，**永远看不到新默认值**（表现为"你根本没改"）；
 *   2. 迁移写过头 → 把用户**手动拖过**的宽度也一起覆盖掉，用户会觉得"我的设置被吃了"。
 * 第 2 种比第 1 种更糟：第 1 种用户能自己发现，第 2 种是静默丢配置。
 *
 * 运行：node tools/verify_ui.mjs    （已挂在 npm run verify 末尾）
 */
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");

let pass = 0, fail = 0;
function ok(cond, msg) {
  if (cond) { pass++; console.log("  PASS  " + msg); }
  else { fail++; console.log("  FAIL  " + msg); }
}
function eq(actual, expected, msg) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected);
  ok(a === b, `${msg}${a === b ? "" : `  (期望 ${b}，实际 ${a})`}`);
}
function section(t) { console.log("\n== " + t + " =="); }

// ---- localStorage 桩：必须在 import store 之前装好（state() 一创建就读它）----
function makeLS(init = {}) {
  const m = new Map(Object.entries(init));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
    dump: () => Object.fromEntries(m),
  };
}
globalThis.localStorage = makeLS();

const { createPinia, setActivePinia } = await import("pinia");
const uiMod = await import(pathToFileURL(path.join(ROOT, "src", "stores", "ui.js")).href);
const {
  LS_KEY, SIDE_MIN, SIDE_MAX, SIDE_DEF, SIDE_DEF_LEGACY, COMPACT_PX, VIEW_MIN,
  clampSideWidth, resolveSideWidth, useUiStore,
} = uiMod;

/** 模拟一次"刷新"：换一个 pinia 实例 → 重新跑 state() 从 localStorage 读值。 */
function boot(raw) {
  if (raw !== undefined) {
    globalThis.localStorage = makeLS(raw === null ? {} : { [LS_KEY]: JSON.stringify(raw) });
  }
  setActivePinia(createPinia());
  return useUiStore();
}

// ---------------------------------------------------------------------
section("A. 常量自洽");
// ---------------------------------------------------------------------
ok(SIDE_MIN < SIDE_MAX, `A1 最小 (${SIDE_MIN}) < 最大 (${SIDE_MAX})`);
ok(SIDE_DEF >= SIDE_MIN && SIDE_DEF <= SIDE_MAX, `A2 默认值 ${SIDE_DEF} 落在 [${SIDE_MIN}, ${SIDE_MAX}] 内`);
eq(SIDE_DEF, 400, "A3 默认宽度 = 400（用户明确要求，改这个数请同步改 index.html 的兜底值）");
eq(SIDE_DEF_LEGACY, 320, "A4 旧默认值记为 320（迁移判定的基准，别再改）");
// COMPACT_PX 由"视口最小宽度 + 默认侧栏宽度"推出；只改默认宽度而不改它，
// 中等窗口下 3D 视口会被悄悄压扁（1040 时视口只剩 640）。
eq(COMPACT_PX, VIEW_MIN + SIDE_DEF, `A5 COMPACT_PX(${COMPACT_PX}) = 视口最小宽(${VIEW_MIN}) + 默认侧栏(${SIDE_DEF})`);

// ---------------------------------------------------------------------
section("B. clampSideWidth：越界/脏值一律兜底");
// ---------------------------------------------------------------------
eq(clampSideWidth(100), SIDE_MIN, "B1 过小 → 夹到最小值");
eq(clampSideWidth(9999), SIDE_MAX, "B2 过大 → 夹到最大值");
eq(clampSideWidth(SIDE_DEF), SIDE_DEF, "B3 合法值原样返回");
eq(clampSideWidth(400.4), 400, "B4 小数 → 四舍五入");
eq(clampSideWidth(400.6), 401, "B5 小数进一位");
eq(clampSideWidth("520"), 520, "B6 数字字符串可以接受");
eq(clampSideWidth("abc"), SIDE_DEF, "B7 非数字字符串 → 默认值");
eq(clampSideWidth(NaN), SIDE_DEF, "B8 NaN → 默认值");
eq(clampSideWidth(undefined), SIDE_DEF, "B9 undefined → 默认值");
eq(clampSideWidth(Infinity), SIDE_DEF, "B10 Infinity → 默认值（不能夹成 760）");
// ★ 这几个是"看着像没值、其实 Number() 会转成 0"的坑：
//   不单独拦的话会被 clamp 到最小值 240，用户看到侧栏突然变得最窄，像是坏了。
eq(clampSideWidth(null), SIDE_DEF, "B11 null → 默认值（Number(null)=0 会被误夹成 240）");
eq(clampSideWidth(""), SIDE_DEF, "B12 空字符串 → 默认值（Number('')=0 同理）");

// ---------------------------------------------------------------------
section("C. resolveSideWidth：只迁移「从没手动调过」的人");
// ---------------------------------------------------------------------
eq(resolveSideWidth(undefined), SIDE_DEF, "C1 全新用户（无记录）→ 新默认值");
eq(resolveSideWidth(320), SIDE_DEF, "C2 老数据 320、无 defW → 视为旧默认、迁移到 400（★ 否则新默认值永远不可见）");
eq(resolveSideWidth(320, 320), SIDE_DEF, "C3 defW=320 且值=320 → 没调过 → 迁移");
eq(resolveSideWidth(500, 320), 500, "C4 老数据 500（用户拖过）→ 原样保留，不覆盖");
eq(resolveSideWidth(240, 320), 240, "C5 老数据 240（用户拖到最窄）→ 原样保留");
eq(resolveSideWidth(760, 320), 760, "C6 老数据 760（用户拖到最宽）→ 原样保留");
eq(resolveSideWidth(320, 400), 320, "C7 ★升级后用户又手动拖回 320 → 必须保留（不能二次迁移）");
eq(resolveSideWidth(300, 400), 300, "C8 升级后拖到 300 → 保留");
eq(resolveSideWidth(null, 400), SIDE_DEF, "C9 存了 null → 默认值");
eq(resolveSideWidth(9999, 320), SIDE_MAX, "C10 越界值 → 夹紧后返回（不是默认值）");
eq(resolveSideWidth("520", 320), 520, "C11 数字字符串 → 保留 520");

// ★ 反证：证明"迁移"这件事真的在做，而不是碰巧结果一样。
//   clampSideWidth 是老的直接路径，它对 320 会原样返回 320 ——
//   所以 state() 必须走 resolveSideWidth，走 clampSideWidth 就等于没改默认值。
eq(clampSideWidth(320), 320, "C12 反证：老路径 clampSideWidth(320) 仍返回 320（故 state 必须走 resolveSideWidth）");
ok(resolveSideWidth(320) !== clampSideWidth(320), "C13 反证：两条路径对 320 的结果必须不同");

// ---------------------------------------------------------------------
section("D. store 端到端：升级 → 持久化 → 再刷新（真的存盘）");
// ---------------------------------------------------------------------
// D1：老用户（存着旧默认值 320）+ 还存了别的偏好
let ui = boot({ sideWidth: 320, sideCollapsed: true, view: "point", camVisible: false });
eq(ui.sideWidth, SIDE_DEF, "D1 老用户刷新后右栏变成新默认值 400");
eq(ui.sideCollapsed, true, "D2 迁移宽度不影响其它偏好（折叠状态仍在）");
eq(ui.view, "point", "D3 迁移宽度不影响当前页面（仍在 point）");
eq(ui.camVisible, false, "D4 迁移宽度不影响摄像头窗口开关");

// D5：store 自己保存一次，payload 里必须带上 defW（下次升级靠它判断"有没有调过"）
ui.setSideWidth(480);
const saved = JSON.parse(globalThis.localStorage.getItem(LS_KEY));
eq(saved.sideWidth, 480, "D5 setSideWidth 已落盘");
eq(saved.defW, SIDE_DEF, "D6 落盘时记下了当时的默认值 defW（迁移判定的依据）");

// D7：再刷新一次 —— 用户手动设的 480 必须原样保住
ui = boot(JSON.parse(globalThis.localStorage.getItem(LS_KEY)));
eq(ui.sideWidth, 480, "D7 再刷新仍是用户手动设的 480");

// D8：★ 关键回归 —— 升级后用户手动拖到 320，之后再刷新不能又被"迁移"到 400
ui.setSideWidth(320);
ui = boot(JSON.parse(globalThis.localStorage.getItem(LS_KEY)));
eq(ui.sideWidth, 320, "D8 升级后手动拖到 320 → 刷新后保持不变（不会二次迁移）");

// D9：损坏的 localStorage 不能崩，且回落到默认值
ui = boot(null);
eq(ui.sideWidth, SIDE_DEF, "D9 存储损坏/为空 → 回落到默认值（不抛异常）");
globalThis.localStorage.setItem(LS_KEY, "{不是合法 json");
ui = boot();
eq(ui.sideWidth, SIDE_DEF, "D10 JSON 解析失败 → 回落到默认值");

// D11：resetSide（双击手柄复位）必须回到**新**默认值
ui = boot({ sideWidth: 700, defW: SIDE_DEF });
ui.resetSide();
eq(ui.sideWidth, SIDE_DEF, "D11 resetSide 复位到新默认值 400");

console.log(`\n结果: ${pass} passed / ${fail} failed  → ${fail ? "FAIL" : "PASS"}`);
process.exit(fail ? 1 : 0);
