import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "node:path";

// 后端默认地址。若你启动 backend 在其它端口，改这里即可；或复制 .env.example → .env 覆盖。
const DEFAULT_BACKEND = "http://127.0.0.1:8003";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget = env.VITE_BACKEND_TARGET || DEFAULT_BACKEND;
  const base = env.VITE_PUBLIC_PATH || "/";

  return {
    base,
    plugins: [vue()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      // 允许任意 Host：内网穿透（ngrok / natapp 等）域名不固定，直接放行 dev 模式
      allowedHosts: true,
      proxy: {
        // 把前端所有 /api/* 请求代理到 backend
        "/api": {
          target: backendTarget,
          changeOrigin: true,
          rewrite: (p) => {
            // 真实 backend 路由不带 /api 前缀，这里统一去掉
            return p.replace(/^\/api/, "");
          },
        },
        // zfile 直链代理：后端把 DB 里的 zfile 绝对 URL 改写为 /zfile/...，
        // 这里转发到本机 zfile。这样内网穿透只需暴露前端端口。
        "/zfile": {
          target: env.VITE_ZFILE_TARGET || "http://127.0.0.1:8081",
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/zfile/, ""),
        },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: mode !== "production",
      chunkSizeWarningLimit: 1024,
    },
  };
});
