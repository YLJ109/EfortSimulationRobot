// =====================================================================
// 共用材质工具（官方数模 与 残影末端工具 共用，避免配色漂移）
//
// ★ 为什么单独成文件：末端执行器（J6 上的吸盘工具）在**官方模型**和**残影**上
//   都要出现，两边必须"长得一模一样"。以前各写一份 → 残影那份逐渐和官方不一致
//   （现场表现：残影跟随看不到 J6 上的工具）。
//   现在只有这一处定义，官方与残影都从这里取。
//
// 依赖方向：manager.js → materials.js ← endEffector.js ← ghost.js（单向，无环）。
// =====================================================================
import * as THREE from "three";

/** 官方数模配色（对照现场真机照片：白色漆面机身 + 深灰五金 + 铝银法兰）。 */
export const SKIN_BODY = 0xe9ecef;
export const SKIN_JOINT = 0x4a5057;
export const SKIN_HARD = 0x23272c;
export const SKIN_METAL = 0xc2c8d0;
export const SKIN_LOGO = "#c8232c";

/** 标准金属漆材质。 */
export function skinMat(color, metalness, roughness) {
  return new THREE.MeshStandardMaterial({ color, metalness, roughness });
}
