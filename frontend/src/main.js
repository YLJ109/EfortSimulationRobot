import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { BRAND } from "./brand.js";
import { useAuthStore } from "./stores/auth.js";

// 标签页标题：从 brand.js 取，避免和静态 <title>（首屏 hydration 前显示）各写一份。
// 静态那份必须同步改，回归里有断言钉着（verify_link.mjs 的 L 段）。
document.title = `${BRAND.name} · ${BRAND.device}`;

const pinia = createPinia();
const app = createApp(App).use(pinia);

// ★ 挂载前先校验一次控制令牌。
//   sessionStorage 里的令牌可能是上一次会话留下的，后端一重启它就作废了；
//   而子组件的 onMounted 早于 App 的 onMounted，若不先验，执行页/审计页会在
//   挂载瞬间拿着死令牌打出一批 401（浏览器控制台必报，JS 侧屏蔽不掉）。
//   先对齐一次，坏令牌当场清掉，之后所有控制请求都会被 apiControl 拦在本地。
//   ★ 最多等 1.5s：后端没起来时 fetchStatus 会静默兜底，但也不能让首屏一直白着。
const auth = useAuthStore(pinia);
const verified = auth.fetchStatus().catch(() => {});
const budget = new Promise((resolve) => setTimeout(resolve, 1500));
Promise.race([verified, budget]).then(() => app.mount("#app"));