// =====================================================================
// 关节归属计算：每个零件由哪个关节驱动。
//
// ★★ 为什么单独抽成纯模块（而不是写在 manager.js 里）★★
//   这是"按状态染色"的判定逻辑，最怕回归且最难在浏览器里发现问题
//   （现象只是"高亮范围大了一点"，不报错、不崩）。抽出来就能用 node
//   直接跑断言，把 J1 到底该亮哪些零件钉死。
//
// ★★ 核心坑（本模块存在的唯一理由）★★
//   串联机器人的关节是**父子链**：J1 是 J2..J6 的祖先节点。
//   所以 `joints[i].traverse()` 会连整条下游链一起收进来，表现为
//   "悬停 J1 全身亮、悬停 J2 亮 J2~J6"。
//   正确做法是反过来：对每个零件沿父链向上找**最近的祖先关节**，
//   那才是真正驱动它的那一个。
// =====================================================================

/**
 * 计算模型里每个 mesh 的驱动关节下标。
 *
 * @param {{root?: object, joints?: object[]}} model buildRobot / buildArticulatedOfficial 的返回
 * @returns {Map<object, number>} mesh → 关节下标（0 基）。基座等不属于任何关节的零件不入表。
 */
export function buildOwnerMap(model) {
  const owner = new Map();
  const joints = (model && model.joints) || [];
  const idxOf = new Map();
  joints.forEach((j, i) => { if (j) idxOf.set(j, i); });
  const root = (model && model.root) || joints[0];
  if (!root) return owner;

  root.traverse((o) => {
    if (!o.isMesh) return;
    // 从零件自己往上走，遇到第一个关节节点就是它的驱动关节。
    // （从 o 开始而不是 o.parent：某些模型里关节节点本身就是个 Mesh）
    for (let n = o; n; n = n.parent) {
      const i = idxOf.get(n);
      if (i !== undefined) { owner.set(o, i); break; }
    }
  });
  return owner;
}

/** 某个关节实际驱动的 mesh 列表（供测试与调试用）。 */
export function meshesOfJoint(model, idx) {
  const owner = buildOwnerMap(model);
  const out = [];
  owner.forEach((j, mesh) => { if (j === idx) out.push(mesh); });
  return out;
}
