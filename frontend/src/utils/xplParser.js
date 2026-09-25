// =====================================================================
// XPL 解析器（前端版）—— 与后端 _parse_xpl 保持一致，供仿真模式使用。
//
// 支持两种输入：
//   ① 简化文本（MOVE / MOVEJ / SUCK / WAIT …）—— 手工粘贴用；
//   ② 真实 XPL 文件（Robox XML：<Xpl-source>…<Pous><Pou><Body>，
//      指令 mjoint / set / wait / while / rem）—— programs/*.XPL 就是这种。
//      ★ 这是"仿真里跑 XPL 文件跑不动"的根因：磁盘上的 411.XPL/200.XPL 是 XML，
//        旧版只按文本行解析 → 整份文件被当成"未知指令"。
//
// 返回统一的 steps 数组：
//   [{ op: 'move', tcp: {x,y,z} } | { op: 'movej', joints: [6] }
//    { op: 'suck', on: bool } | { op: 'wait', dwell_ms: number }
//    { op: 'unknown', raw: string, line: number }]
//
// 用法：
//   import { parseXpl } from '../utils/xplParser.js';
//   const { kind, name, steps } = await parseXpl(xplText);
// =====================================================================

/**
 * 解析 XPL 内容（自动区分 Robox XML 与简化文本）
 * @param {string} raw - XPL 文件文本内容
 * @param {string} name - 程序名（可选，默认从文件名推导）
 * @returns {{kind: string, name: string, steps: Array}}
 */
export async function parseXpl(raw, name = 'xpl') {
  const text = String(raw == null ? '' : raw);
  if (/<\s*Xpl-source|<\s*Pous|<\s*HostEnvironment/i.test(text)
      && /<mjoint|<set\b|<Body/i.test(text)) {
    return parseRoboxXml(text, name);
  }
  return parseSimplified(text, name);
}

/** 从 POINTJ(a,b,c,...) / POINTC(x,y,z,...) 里取数值数组。 */
function numsOf(expr) {
  const m = String(expr).match(/\(([^)]*)\)/);
  if (!m) return [];
  return m[1].split(',').map((s) => parseFloat(s))
    .map((v) => (isNaN(v) ? null : v));
}

/**
 * 解析 Robox XPL（XML）。按出现顺序提取叶子指令：
 *   <rem> 注释丢弃；<while> 循环在仿真里不支持 → 跳过（不阻断后续步骤）；
 *   <set> 写 DOut 视为吸/放气；<wait> 视为等待；<mjoint> 视为运动
 *   （POINTJ→关节、POINTC→笛卡尔）。
 */
function parseRoboxXml(text, name) {
  const steps = [];
  // 逐条叶子指令匹配（非贪婪，能正确处理 <rem><text/></rem> 这类内嵌）
  const re = /<(rem|set|mjoint|wait|while|movej|movel)\b[^>]*>([\s\S]*?)<\/\1>/gi;
  let m;
  let guard = 0;
  while ((m = re.exec(text)) !== null && guard++ < 5000) {
    const tag = m[1].toLowerCase();
    const inner = m[2];
    if (tag === 'rem' || tag === 'while') continue;    // 注释 / 循环：仿真跳过

    if (tag === 'set') {
      const dest = (inner.match(/<dest>([\s\S]*?)<\/dest>/i) || [])[1] || '';
      const expr = (inner.match(/<expr>([\s\S]*?)<\/expr>/i) || [])[1] || '';
      if (/DOut|ctr|grip|suck/i.test(dest)) {
        steps.push({ op: 'suck', on: /true/i.test(expr) });
      }
      continue;
    }

    if (tag === 'wait') {
      // Robox 的 wait 多为"等条件"，无固定时长；仿真给一个可视短停
      steps.push({ op: 'wait', dwell_ms: 300 });
      continue;
    }

    // mjoint / movej / movel
    const target = (inner.match(/<target>([\s\S]*?)<\/target>/i) || [])[1] || '';
    const nums = numsOf(target);
    if (/POINTJ/i.test(target)) {
      const six = nums.slice(0, 6);
      if (six.length === 6 && six.every((v) => v !== null)) {
        steps.push({ op: 'movej', joints: six });
      } else {
        steps.push({ op: 'unknown', raw: ('POINTJ ' + target).trim(), line: 0 });
      }
    } else if (/POINTC/i.test(target)) {
      const p = nums.slice(0, 3);
      if (p.length === 3 && p.every((v) => v !== null)) {
        steps.push({ op: 'move', tcp: { x: p[0], y: p[1], z: p[2] } });
      } else {
        steps.push({ op: 'unknown', raw: ('POINTC ' + target).trim(), line: 0 });
      }
    }
  }
  if (!steps.length) {
    throw new Error('XPL（Robox XML）里没有可仿真的运动/IO 指令');
  }
  return { kind: 'program', name, steps, format: 'robox-xml' };
}

/** 解析简化文本格式。 */
function parseSimplified(raw, name) {
  const lines = String(raw).split(/\r?\n/);
  const steps = [];
  let lineNo = 0;

  for (const line of lines) {
    lineNo++;
    // 去除注释（支持 # ; //）：取各注释符首次出现的有效位置的最小值
    const idxs = [line.indexOf('#'), line.indexOf(';'), line.indexOf('//')]
      .filter((i) => i >= 0);
    const commentIdx = idxs.length ? Math.min(...idxs) : line.length;
    const s = line.slice(0, commentIdx).trim();
    if (!s) continue;

    const toks = s.split(/\s+/);
    const cmd = toks[0].toUpperCase();

    const f = (i) => {
      const v = parseFloat(toks[i]);
      return isNaN(v) ? null : v;
    };

    if (['MOVE', 'MOVEP', 'MOVL', 'MOV'].includes(cmd)) {
      const x = f(1), y = f(2), z = f(3);
      if (x === null || y === null || z === null) {
        steps.push({ op: 'unknown', raw: s, line: lineNo });
      } else {
        steps.push({ op: 'move', tcp: { x, y, z } });
      }
    } else if (['MOVEJ', 'MOVJ'].includes(cmd)) {
      const vals = [f(1), f(2), f(3), f(4), f(5), f(6)];
      if (vals.some(v => v === null) || vals.length !== 6) {
        steps.push({ op: 'unknown', raw: s, line: lineNo });
      } else {
        steps.push({ op: 'movej', joints: vals });
      }
    } else if (['SUCK', 'GRIP', '吸气', '放气'].includes(cmd)) {
      const arg = (toks[1] || '').toUpperCase();
      const on = arg === 'ON' || arg === '1' || arg === 'OPEN' || arg === '开' || cmd === '吸气';
      steps.push({ op: 'suck', on });
    } else if (['WAIT', 'DELAY', '等待'].includes(cmd)) {
      const ms = f(1);
      if (ms === null) {
        steps.push({ op: 'unknown', raw: s, line: lineNo });
      } else {
        steps.push({ op: 'wait', dwell_ms: Math.max(0, Math.round(ms)) });
      }
    } else {
      steps.push({ op: 'unknown', raw: s, line: lineNo });
    }
  }

  if (!steps.length) {
    throw new Error('XPL 文件没有任何可执行指令');
  }

  return { kind: 'program', name, steps };
}

/**
 * 加载并解析 .xpl 文件
 * @param {string} filename - 文件名（相对于 programs 目录）
 * @returns {{kind: string, name: string, steps: Array}}
 */
export async function loadAndParseXpl(filename) {
  try {
    const baseUrl = (await import('../config.js')).apiUrl('');
    const url = `${baseUrl}/control/files/${encodeURIComponent(filename)}?raw=1`;
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`无法加载文件: ${response.status}`);
    }
    const raw = await response.text();
    return parseXpl(raw, filename);
  } catch (e) {
    throw new Error(`加载/解析 XPL 失败: ${e.message}`);
  }
}

/**
 * 将 XPL steps 转换为仿真执行器可用的格式
 * 每个 step 转为 { type: 'move'|'movej'|'suck'|'wait', ... }
 */
export function xplStepsToSim(steps) {
  return steps.map((s, idx) => {
    const base = { index: idx + 1, op: s.op };
    if (s.op === 'move' && s.tcp) {
      return { ...base, type: 'move', tcp: s.tcp };
    }
    if (s.op === 'movej' && s.joints) {
      return { ...base, type: 'movej', joints: s.joints };
    }
    if (s.op === 'suck') {
      return { ...base, type: 'suck', on: s.on };
    }
    if (s.op === 'wait') {
      return { ...base, type: 'wait', dwell_ms: s.dwell_ms };
    }
    return { ...base, type: 'unknown', raw: s.raw, line: s.line };
  });
}