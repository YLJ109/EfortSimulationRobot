// =====================================================================
// J6 末端执行器（真实工具：法兰 + 两块矩形立板 + 两个红色真空吸盘）
//
// ★★ 单一来源：**官方数模**（manager.js 的 tcpNode）与**残影 Ghost**
//    （ghost.js 的 J6 法兰节点）都调用本函数 —— 从此两边外观不可能漂移。
//    以前的教训：残影自己手写了一份简化版，挂着挂着就和官方不一致，
//    现场表现就是"残影跟随看不到 J6 上的那个工具"。
//
// ★ 尺度约定（务必注意，写错就是 1000 倍事故）：
//   本函数的几何尺寸是"近似 mm 的设计值"，内部再乘 EE_SCALE 放大；
//   它是给**官方那条 0.001 缩放链**用的（官方 root.scale = 0.001）。
//   残影的程序化模型本身是"米"，所以残影侧挂载时必须再乘 0.001
//   （见 ghost.js 的调用点），否则会放大 1000 倍。
//
// 依赖方向：endEffector.js → materials.js（单向，无环）。
// =====================================================================
import * as THREE from "three";
import { skinMat, SKIN_HARD, SKIN_METAL } from "./materials.js";

const EE_FACE = 8;
const EE_SCALE = 2;
// ★ P1-E13：原 const EE_OPEN0 = 46（夹爪默认开口）本文件从未引用 —— 实际开口量
//   直接写在 buildEndEffector() 的手指几何里，留一个"看似生效的常量"反而误导。

/**
 * 构建 J6 末端工具。
 * @returns {{group: THREE.Group}} group 的局部原点 = J6 法兰面；调用方负责挂到
 *          正确的法兰节点上（官方 tcpNode / 残影 flangeNode）。
 */
export function buildEndEffector() {
  const outer = new THREE.Group();
  outer.name = "end-effector";
  // J6 末端：吸盘默认就沿法兰 +z（前方）开口，避免多余旋转导致朝向错乱。仅改末端模型，不动机器人关节。
  outer.rotation.set(0, 0, 0);
  const g = new THREE.Group();
  g.scale.setScalar(EE_SCALE);
  outer.add(g);

  // 本局部坐标为设计尺寸(近似 mm)，整体经 g 放大 EE_SCALE 倍渲染。
  const m = (c, mr, mm) => skinMat(c, mr, mm);

  // ---- 法兰定位盘 + 6 颗螺栓（沿用） ----
  const plate = new THREE.Mesh(new THREE.CylinderGeometry(30, 30, 10, 36), m(SKIN_METAL, 0.8, 0.28));
  plate.rotation.x = Math.PI / 2;
  plate.position.z = EE_FACE + 5;
  g.add(plate);
  for (let k = 0; k < 6; k++) {
    const a = (k / 6) * Math.PI * 2;
    const b = new THREE.Mesh(new THREE.CylinderGeometry(3.5, 3.5, 6, 12), m(SKIN_HARD, 0.85, 0.3));
    b.rotation.x = Math.PI / 2;
    b.position.set(Math.cos(a) * 23, Math.sin(a) * 23, EE_FACE + 11);
    g.add(b);
  }

  // ---- 真空吸盘夹具：两块大矩形立板贴住法兰，每块外表面各装一个吸盘，垂直于矩形中心、开口朝外 ----
  const plateW = 10;             // 立板厚度(x)
  const plateH = 50;             // 立板高度(y)
  const plateD = 90;             // 立板深度(z，沿法兰轴向)
  const plateGap = 30;           // 两块立板内间距(x方向净空)
  const plateZ = EE_FACE + plateD / 2;  // 立板中心z

  // 1) 两块大矩形立板，分置 x±，贴住法兰前端
  for (let s = -1; s <= 1; s += 2) {
    const plate = new THREE.Mesh(
      new THREE.BoxGeometry(plateW, plateH, plateD),
      m(0x9aa2ab, 0.6, 0.4)
    );
    plate.position.set(s * (plateW / 2 + plateGap / 2), 0, plateZ);
    g.add(plate);
  }

  // 2) 每块立板外表面各装一个红色吸盘，吸盘垂直于矩形中心、开口朝外(±x)
  function cup(side) {
    // side = -1(左板, 开口朝 -x) 或 +1(右板, 开口朝 +x)
    const grp = new THREE.Group();
    const xOuter = side * (plateW + plateGap / 2);  // 立板外表面 x 坐标

    // 阀体：圆柱贴在立板外表面
    const valve = new THREE.Mesh(
      new THREE.CylinderGeometry(7, 7, 12, 16),
      m(0x8a8f95, 0.6, 0.5)
    );
    valve.rotation.z = Math.PI / 2;   // 圆柱轴沿 x
    valve.position.set(xOuter + side * 6, 0, plateZ);
    grp.add(valve);

    // 波纹吸盘：圆锥开口朝外
    const bell = new THREE.Mesh(
      new THREE.ConeGeometry(15, 20, 24),
      m(0x7a1f1f, 0.15, 0.25)
    );
    bell.rotation.z = side * Math.PI / 2;  // 圆锥开口朝 ±x
    bell.position.set(xOuter + side * 18, 0, plateZ);
    grp.add(bell);

    // 橡胶圈
    const rim = new THREE.Mesh(
      new THREE.CylinderGeometry(16.5, 16.5, 3, 24),
      m(0x5a1717, 0.25, 0.5)
    );
    rim.rotation.z = Math.PI / 2;
    rim.position.set(xOuter + side * 28, 0, plateZ);
    grp.add(rim);

    return grp;
  }
  g.add(cup(-1));   // 左板吸盘
  g.add(cup(1));    // 右板吸盘

  // 吸盘夹具无"开合指"：吸气/放气由业务信号驱动，无需张开动画
  return { group: outer };
}
