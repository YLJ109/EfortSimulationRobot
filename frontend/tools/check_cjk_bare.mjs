// tools/check_cjk_bare.mjs —— 引号守卫（挂 npm run verify）
//
// 背景：本项目里 `guide.js` / `shortcuts.js` / `brand.js` / 各 .vue 塞了大量中文文案。
// 一旦在双引号串里再写 ASCII 双引号，例如：
//     { text: "别用连续点动"蹭"过去。" }
// Vite 与各 headless 测试**都不会提前报错**，只在浏览器里炸 SyntaxError（整页白屏）。
// 这个文件已被同一问题坑过 4 次，所以做成硬守卫。
//
// 判据：走一遍最小词法状态机，剥掉【字符串 / 模板串 / 行注释 / 块注释】的内容，
// 剩下的是"真正的代码"。本项目代码位不允许出现 CJK（标识符全是 ASCII），
// 所以代码位残留 CJK ⇒ 一定有引号漏配对（或注释符写错）。
//
// 用法：
//   node tools/check_cjk_bare.mjs                    # 自动扫描 src/ 下所有 .js 与 .vue 的 script 段
//   node tools/check_cjk_bare.mjs a.js b.vue         # 只查指定文件
// 退出码：0 = 干净；1 = 命中。

import fs from "node:fs";
import path from "node:path";

const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/;
const isCjk = (ch) => !!ch && CJK.test(ch);

/** `/` 在这些字符之后是**正则字面量**起点（否则是除号）。 */
const REGEX_OK_CHARS = new Set([
  "", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%", "<", ">", "~", "^",
]);
/** 这些关键字之后 `/` 也是正则起点（`return /x/`、`typeof /x/`…）。 */
const REGEX_OK_WORDS = new Set([
  "return", "case", "typeof", "instanceof", "in", "of", "new", "delete", "void",
  "do", "else", "yield", "await", "throw",
]);

/**
 * 走一遍状态机。
 *
 * 关键点：
 *  1) 模板串的 `${ ... }` 里是**代码上下文**，里面可以再出现字符串/注释/模板串，
 *     所以不能"进了模板串就吞到底"，必须用显式栈记住从哪个模板串出来。
 *  2) 正则字面量 `/失败|权限/` 里的中文是**字符串内容**，不是代码位。
 *     靠"前一个有效字符是否允许正则"这条常规启发式区分 `/` 与除号。
 *
 * @returns {{ stray: Array<{line:number, text:string}> }}
 */
export function scan(src) {
  const N = src.length;
  let i = 0;
  let state = "code"; // code | lc | bc | sq | dq | tpl | regex
  let line = 1;
  let inClass = false; // 正则字符组 [...] 内
  /** 进入 ${...} 时的调用栈，每项是 { brace: number }（用于配平对象字面量的花括号） */
  const tplStack = [];
  const stray = [];
  const seen = new Set();
  /** 最近一段"有效代码字符"（去掉空白/注释），只保留尾部，用于判断 `/` 的语义 */
  let sig = "";

  const pushSig = (ch) => {
    if (!/\s/.test(ch)) {
      sig = (sig + ch).slice(-240);
    }
  };

  const mark = (ch) => {
    pushSig(ch);
    if (!isCjk(ch)) return;
    if (seen.has(line)) return;
    seen.add(line);
    stray.push({ line, text: (src.split("\n")[line - 1] || "").trim() });
  };

  const regexAllowed = () => {
    const prev = sig.slice(-1);
    const w = /([A-Za-z_$][A-Za-z0-9_$]*)$/.exec(sig);
    if (w && REGEX_OK_WORDS.has(w[1])) return true;
    return REGEX_OK_CHARS.has(prev);
  };

  while (i < N) {
    const c = src[i];
    const c2 = src[i + 1];
    if (c === "\n") line += 1;

    if (state === "code") {
      if (c === "/" && c2 === "/") { state = "lc"; i += 2; continue; }
      if (c === "/" && c2 === "*") { state = "bc"; i += 2; continue; }
      if (c === "/" && regexAllowed()) { state = "regex"; inClass = false; pushSig(c); i += 1; continue; }
      if (c === "'") { state = "sq"; i += 1; continue; }
      if (c === '"') { state = "dq"; i += 1; continue; }
      if (c === "`") { state = "tpl"; i += 1; continue; }
      const top = tplStack[tplStack.length - 1];
      if (top && c === "{") { top.brace += 1; pushSig(c); i += 1; continue; }
      if (top && c === "}") {
        if (top.brace === 0) { tplStack.pop(); state = "tpl"; i += 1; continue; }
        top.brace -= 1; pushSig(c); i += 1; continue;
      }
      mark(c);
      i += 1;
      continue;
    }

    if (state === "lc") {
      if (c === "\n") state = "code";
      i += 1;
      continue;
    }

    if (state === "bc") {
      if (c === "*" && c2 === "/") { state = "code"; i += 2; continue; }
      i += 1;
      continue;
    }

    if (state === "regex") {
      if (c === "\\") { i += 2; continue; }
      if (c === "[") { inClass = true; i += 1; continue; }
      if (c === "]") { inClass = false; i += 1; continue; }
      if (c === "/" && !inClass) {
        // 吃掉修饰符
        let j = i + 1;
        while (j < N && /[a-z]/i.test(src[j])) j += 1;
        sig = (sig + "/" + src.slice(i + 1, j)).slice(-240);
        i = j;
        state = "code";
        continue;
      }
      if (c === "\n") { state = "code"; i += 1; continue; } // 正则不跨行，兜底退出
      i += 1;
      continue;
    }

    // 字符串 / 模板串：先吃转义
    if (c === "\\") { i += 2; continue; }

    if (state === "sq") { if (c === "'") state = "code"; i += 1; continue; }
    if (state === "dq") { if (c === '"') state = "code"; i += 1; continue; }

    if (state === "tpl") {
      if (c === "`") { state = "code"; i += 1; continue; }
      if (c === "$" && c2 === "{") { tplStack.push({ brace: 0 }); state = "code"; i += 2; continue; }
      i += 1;
      continue;
    }

    i += 1;
  }

  return { stray };
}

/** 取 .vue 里的 <script> 段，并把行号偏移带出来。 */
function extractVue(source) {
  const m = /<script\b[^>]*>([\s\S]*?)<\/script>/i.exec(source);
  if (!m) return null;
  const before = source.slice(0, m.index + m[0].indexOf(m[1]));
  const offset = (before.match(/\n/g) || []).length;
  return { code: m[1], offset };
}

/** 递归收集 src/ 下的 .js / .vue */
function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) {
      if (ent.name === "node_modules" || ent.name.startsWith(".")) continue;
      walk(p, out);
    } else if (/\.(js|mjs|vue)$/.test(ent.name)) {
      out.push(p);
    }
  }
  return out.sort();
}

let files = process.argv.slice(2);
if (!files.length) files = walk("src");

if (!files.length) {
  console.error("check_cjk_bare: 没找到待检查文件");
  process.exit(2);
}

let bad = 0;
for (const f of files) {
  const raw = fs.readFileSync(f, "utf8");
  let code = raw;
  let offset = 0;
  if (f.endsWith(".vue")) {
    const ex = extractVue(raw);
    if (!ex) { console.log(`skip  ${f}（无 <script> 段）`); continue; }
    code = ex.code;
    offset = ex.offset;
  }
  const { stray } = scan(code);
  if (stray.length) {
    bad += 1;
    console.error(`FAIL  ${f}`);
    for (const s of stray.slice(0, 12)) {
      const ln = s.line + offset;
      const txt = f.endsWith(".vue") ? (raw.split(/\r?\n/)[ln - 1] || "").trim() : s.text;
      console.error(`  L${ln}  代码位出现中文（多半是串里嵌了 ASCII 引号，请改用「」）: ${txt.slice(0, 130)}`);
    }
    if (stray.length > 12) console.error(`  … 另有 ${stray.length - 12} 处`);
  } else {
    console.log(`ok    ${f}`);
  }
}

console.log(`--- check_cjk_bare: ${files.length - bad}/${files.length} 通过 ---`);
process.exit(bad ? 1 : 0);
