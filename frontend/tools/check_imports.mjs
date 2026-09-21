// =====================================================================
// 静态守卫：跨模块导出的常量"用到但没导入"。
//
// 为什么需要它：
//   `vite build` 对未声明标识符**不会报错**（rollup 只当全局变量），构建照样 EXIT=0；
//   只有浏览器真正执行到那一行才炸 ——
//       ReferenceError: PLATFORM_SIZE is not defined
//   此时界面直接黑屏/散架，而 headless 回归（只 import cell.js / safety.js，
//   不加载需要 WebGL 的 manager.js）**完全测不到**。
//   本项目已真实踩过一次：manager.js 写了 `size: PLATFORM_SIZE` 但 import 里只有
//   { buildCell, PLATFORM_H } → 生产包运行时报错。
//
// 规则：若某文件用到了"另一个文件 export const 的名字"，而本文件既没 import 它、
//       也没自己声明它 → FAIL。
//
// 用法：node tools/check_imports.mjs [src目录]（默认 ./src）
// =====================================================================
import fs from "node:fs";
import path from "node:path";

const SRC = path.resolve(process.argv[2] || path.join(process.cwd(), "src"));

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(js|mjs|ts|vue)$/.test(e.name)) out.push(p);
  }
  return out;
}

/** 去掉注释 + 字符串字面量，只留"代码"文本（避免注释/文案里的名字造成假阳性）。 */
function codeOnly(src) {
  let out = "";
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i], d = src[i + 1];
    if (c === "/" && d === "/") { while (i < n && src[i] !== "\n") i += 1; continue; }
    if (c === "/" && d === "*") {
      i += 2;
      while (i < n && !(src[i] === "*" && src[i + 1] === "/")) i += 1;
      i += 2;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      const q = c;
      i += 1;
      while (i < n) {
        if (src[i] === "\\") { i += 2; continue; }
        if (src[i] === q) { i += 1; break; }
        i += 1;
      }
      out += " ";
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

/** .vue 的 <style> 是 CSS，里面的类名可能和常量同名 → 去掉，避免假阳性。 */
function stripStyleBlocks(s) {
  return s.replace(/<style[\s\S]*?<\/style>/gi, " ");
}

/** ★ import 语句必须用**保留字符串的原文**解析：模块路径本身就是字符串，
 *   去字符串后 `from "./cell.js"` 会整段消失，导致"已导入"被判成"没导入"。 */
function importedNames(rawWithStrings) {
  const names = new Set();
  const re = /import\s+([\s\S]*?)\s+from\s*["'][^"']+["']/g;
  for (const m of rawWithStrings.matchAll(re)) {
    const clause = m[1].trim();
    const ns = clause.match(/\*\s+as\s+([A-Za-z_$][\w$]*)/);
    if (ns) names.add(ns[1]);
    const braced = clause.match(/\{([\s\S]*)\}/);
    if (braced) {
      for (const part of braced[1].split(",")) {
        const t = part.trim();
        if (!t) continue;
        const as = t.split(/\s+as\s+/);
        names.add((as[1] || as[0]).replace(/\s+/g, ""));
      }
    }
    const rest = clause.replace(/\{[\s\S]*\}/, "").replace(/\*\s+as\s+[\w$]+/, "");
    const def = rest.replace(/,/g, " ").trim().split(/\s+/).filter(Boolean)[0];
    if (def) names.add(def);
  }
  return names;
}

const files = walk(SRC);
if (!files.length) {
  console.error(`未找到源文件: ${SRC}`);
  process.exit(2);
}
const rel = (f) => path.relative(SRC, f).split(path.sep).join("/");

const parsed = files.map((f) => {
  const raw = stripStyleBlocks(fs.readFileSync(f, "utf8"));
  return { file: rel(f), raw, code: codeOnly(raw) };
});

// ---- 1) 收集"被 export 出去的 const 名字" ----
const RE_EXPORT = /export\s+const\s+([A-Za-z_$][\w$]*)\s*=/g;
const exporters = new Map(); // name -> Set(relFile)
for (const p of parsed) {
  for (const m of p.code.matchAll(RE_EXPORT)) {
    if (!exporters.has(m[1])) exporters.set(m[1], new Set());
    exporters.get(m[1]).add(p.file);
  }
}

// ---- 2) 逐文件核对 ----
const fails = [];
for (const p of parsed) {
  const imported = importedNames(p.raw);
  for (const [name, owners] of exporters) {
    if (owners.has(p.file)) continue;      // 本文件就是定义处
    if (imported.has(name)) continue;      // 已正确导入
    const local = new RegExp(`(?:const|let|var|function|class)\\s+${name}\\b`);
    if (local.test(p.code)) continue;      // 本文件自己声明了同名变量
    const used = new RegExp(`(?<![.\\w$])${name}(?![\\w$])`);
    const m = used.exec(p.code);
    if (m) {
      fails.push({
        file: p.file,
        name,
        from: [...owners].join(", "),
        line: p.code.slice(0, m.index).split("\n").length,
      });
    }
  }
}

console.log("== 跨模块常量导入检查（防 ReferenceError） ==");
console.log(`  扫描 ${parsed.length} 个源文件 / ${exporters.size} 个导出常量`);
for (const f of fails) {
  console.log(`  FAIL  ${f.file}:${f.line} 使用了 ${f.name}，但既未导入也未声明（定义在 ${f.from}）`);
  console.log(`         → 补进 import 语句，否则浏览器运行时报 ${f.name} is not defined`);
}
const badFiles = new Set(fails.map((x) => x.file)).size;
if (!fails.length) console.log("  PASS  无\"用了没导入\"的跨模块常量");
console.log(`结果: ${parsed.length - badFiles} 文件干净 / ${badFiles} 文件有问题  → ${fails.length ? "FAIL" : "PASS"}`);
process.exit(fails.length ? 1 : 0);
