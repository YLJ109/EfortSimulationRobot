// =====================================================================
// ESLint 扁平配置 —— ★ 审计修复 P1-E13（建立 lint：ruff + eslint + pytest.ini）
//
// 用法：npm run lint            # 只看问题
//       npm run lint:fix        # 自动修能修的
//
// 定位和 backend/ruff.toml 一致：**先开"能指出真问题"的规则，且当前必须全绿**，
// 不做一次性清理出几百条、第二天没人看的整改。本文件里的每条 rule 关闭/放宽
// 都写了原因 —— 关掉但不写理由，等于埋坑。
//
// 解析链：
//   @eslint/js          —— 基础 JS 规则（recommended）
//   eslint-plugin-vue   —— Vue3 SFC 语法级 + 模板最佳实践（flat/vue3-recommended）
//   vue-eslint-parser   —— 由 eslint-plugin-vue 扁平配置自带，无需单独声明
// =====================================================================

import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";

export default [
  { ignores: ["dist/**", "node_modules/**", "public/**", "tools/**", "**/*.min.js"] },

  js.configs.recommended,
  // 注意：eslint-plugin-vue v10 的扁平配置键是 `flat/recommended`
  // （不存在 `flat/vue3-recommended` —— 用错会直接 TypeError，见本文件顶部注释的历史）。
  ...pluginVue.configs["flat/recommended"],

  {
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: false } },
    },

    rules: {
      // ---- 关闭/放宽的规则（每条都有理由，禁止"顺手再关一条"）----
      //
      // ① no-undef：前端运行在浏览器，全局（window/document/localStorage/fetch/WebSocket…）
      //    数量极多且各构建期注入不同；更麻烦的是 .vue 模板里编译产物会引用运行时助手。
      //    真正"用错名字"的漏网之鱼由 ② no-unused-vars 的残留 + vite build + tools/check_imports.mjs
      //    三道守卫兜住（check_imports 专门抓"用了别的文件 export 的名字却没 import"）。
      "no-undef": "off",

      // ② 未使用的变量：改成 warn —— 现场调试常留临时变量，直接 error 会逼人写 `_` 逃逸，
      //    反而掩盖真的忘了删的东西。CI 里只把 error 计入失败。
      //    ignoreRestSiblings：`const { type, t, ...rest } = payload` 这种"先扔掉几个键
      //    再展开剩余"的写法里，type/t 本来就不会被读 —— 它们不是死代码。
      "no-unused-vars": ["warn", {
        args: "after-used",
        argsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
        caughtErrors: "none",
        ignoreRestSiblings: true,
      }],

      // ③ Vue 模板里的属性顺序 / 单词组件名：纯风格，与本项目现有写法冲突，收益为 0。
      "vue/attributes-order": "off",
      "vue/multi-word-component-names": "off",

      // ④ v-html 是本项目内嵌中文富文本说明的既有用法（引导条/帮助文案），
      //    内容全部来自本地常量而非用户输入；真正的 XSS 面在后端回显处（审计 P2）。
      "vue/no-v-html": "off",

      // ⑤ 本项目模板统一 2 空格缩进（与 index.html 全站 CSS 同风格），
      //    让 eslint 抢格式会和手工断行 + 中文注释打架。
      "vue/html-indent": "off",
      "vue/max-attributes-per-line": "off",
      "vue/html-self-closing": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/multiline-html-element-content-newline": "off",
      "vue/html-closing-bracket-newline": "off",
      "vue/first-attribute-linebreak": "off",
    },
  },

];
