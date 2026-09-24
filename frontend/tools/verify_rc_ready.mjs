// =====================================================================
// 真机链路就绪卡回归（headless，源码扫描）
//
// 钉死后续最容易回归的几点：
//  A. store 通道唯一：/rc-status 只走 apiUrl（公开只读，不带令牌）；
//     一键就绪只走 apiControl("/ready")（写寄存器必须带控制令牌）。
//     ★ 坑27 守卫：apiUrl/apiControl 会自动补 /api 前缀，路径绝不能再带 /api。
//  B. 档位声明不另开通道：claimMode 必须委托 link.claimMode()。
//  C. 卡片挂载：点位执行页挂载、程序执行页改用就绪盒读 rc.ready；状态行七项齐全；
//     一键就绪按钮在「无令牌或机器人未连接（canControl）/ 双闸未开 / 触发位占用」时必须禁用。
//  D. 图标名必须真实存在于 Icon.vue（动态 name 拼错只会静默空白）。
//  E. 后端契约：/rc-status 返回 ready/real_enabled/service_program；
//     /ready 挂 require_control。
//
// 运行： node tools/verify_rc_ready.mjs
// =====================================================================
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(join(root, p), "utf8");

let pass = 0, fail = 0;
function ok(name, cond, extra = "") {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (extra ? "   → " + extra : "")); }
}

console.log("== 真机链路就绪卡回归 ==");

const store = read("src/stores/rcReady.js");
const card = read("src/components/RcReadyCard.vue");
const pointView = read("src/components/PointExecView.vue");
const progView = read("src/components/ProgramExecView.vue");
const icons = read("src/components/Icon.vue");
const robotApi = read("../backend/app/api/robot.py");
const robotStore = read("src/stores/robot.js");
const wsNet = read("src/net/ws.js");

// ---------- A. store 通道唯一 + 路径不带 /api 前缀 ----------
ok("rc-status 走 apiUrl（公开只读，无需令牌）",
  store.includes("fetch(apiUrl(\"/rc-status\"))"));
ok("一键就绪走 apiControl /ready（写寄存器须带控制令牌）",
  store.includes("apiControl(\"/ready\""));
ok("apiUrl 路径不带 /api 前缀（坑27：会变成 /api/api/...）",
  !/apiUrl\(\s*["'`]\/api\//.test(store));
ok("apiControl 路径不带 /api 前缀",
  !/apiControl\(\s*["'`]\/api\//.test(store));
ok("轮询周期常量 RC_POLL_MS 已定义（现为 WS 新鲜度窗口）", /export const RC_POLL_MS\s*=\s*\d+/.test(store));
ok("数据经 WS 注入而非轮询：net/ws.js 分发 rc_status → robot store → rcReady.ingest",
  wsNet.includes('msg.type === "rc_status"')
  && robotStore.includes("useRcReadyStore().ingest(rc)")
  && store.includes("ingest(payload)"));
ok("rcReady 不再起定时轮询（无 setInterval 残留）", !/setInterval/.test(store));

// ---------- B. 档位声明唯一通道 ----------
ok("claimMode 委托 link.claimMode（档位声明绝不另写一份）",
  /const link = useLinkStore\(\);/.test(store)
  && /link\.claimMode\(mode\)/.test(store));

// ---------- C. 卡片挂载与门控 ----------
ok("点位执行页挂载 RcReadyCard", pointView.includes("<RcReadyCard />"));
ok("点位执行页 import RcReadyCard", pointView.includes("import RcReadyCard from"));
// ★ 程序执行页已按现场要求把「真机链路卡 + 控制权限卡」合成一个就绪提示盒：
//   不再挂 RcReadyCard，只读 useRcReadyStore().ready 给出一句结论（见 ProgramExecView）。
ok("程序执行页不再挂 RcReadyCard（改用就绪提示盒）", !progView.includes("<RcReadyCard />"));
ok("程序执行页不再 import RcReadyCard 组件", !progView.includes("import RcReadyCard from"));
ok("程序执行页就绪盒仍读 rc.ready 合成结论",
  progView.includes("useRcReadyStore") && /rc\.ready\s*&&/.test(progView));

for (const k of ["双闸", "档位", "伺服", "报警", "急停", "程序", "点动"]) {
  ok(`状态行「${k}」存在`, card.includes(`k: "${k}"`));
}
ok("有一键就绪按钮", card.includes("一键就绪"));
ok("就绪中反馈文案", card.includes("就绪中"));
ok("一键就绪在无控制令牌 / 机器人未连接时禁用（判据只此一处：canControl）",
  /const canControl = computed\(\(\) => auth\.controlActive && robot\.connected\)/.test(card)
  && /:disabled="[^"]*!canControl/.test(card));
ok("一键就绪在双闸未开时禁用", /:disabled="[^"]*!rc\.realEnabled/.test(card));
ok("一键就绪在触发位占用时禁用（上一发未收尾绝不执行）",
  /:disabled="[^"]*rc\.jogTrig/.test(card));
ok("就绪执行中禁用重复触发", /:disabled="[^"]*rc\.busy/.test(card));

// 卡片语义：实测档位反转 —— AUTO/远程可动，T1/T2 拒绝
ok("store 语义反转：仅 auto/remote 可点动",
  /m === "auto" \|\| m === "remote"/.test(store));
ok("手动档文案标注 T1/T2 指令无效", card.includes("手动（T1/T2）") || store.includes("手动（T1/T2）"));

// ---------- D. 图标名真实存在 ----------
const iconNames = [...icons.matchAll(/^\s{2}(\w+):\s*'/gm)].map((m) => m[1]);
const usedIcons = [...card.matchAll(/<Icon[^>]*name="(\w+)"/g)].map((m) => m[1]);
const dynamicIcons = [...card.matchAll(/:name="rc\.lastResult\.ok \? '(\w+)' : '(\w+)'"/g)]
  .flatMap((m) => [m[1], m[2]]);
const all = [...new Set([...usedIcons, ...dynamicIcons])];
for (const n of all) {
  ok(`图标「${n}」存在于 Icon.vue`, iconNames.includes(n));
}

// ---------- E. 后端契约 ----------
ok("后端 /rc-status 返回 real_enabled", robotApi.includes('"real_enabled"'));
ok("后端 /rc-status 返回 service_program", robotApi.includes('"service_program"'));
ok("后端就绪判定 ready（伺服+程序+运行+自动/远程+无报警+非急停）",
  robotApi.includes('snap["ready"]'));
ok("后端 /ready 挂 require_control 门控",
  /def ready\(.*Depends\(require_control\)/s.test(robotApi));

console.log("");
console.log("结果: " + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
