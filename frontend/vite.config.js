import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 构建产物放 dist/, 由后端 FastAPI 托管 (base 用相对路径, 适配任意部署前缀)。
// 开发: vite dev(5173) 通过 proxy 把 /api 与 /ws 转发到后端 8000, 同源免 CORS。
export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
