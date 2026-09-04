import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "node:path";

// 后端默认地址。若你启动 backend 在其它端口，改这里即可；或复制 .env.example → .env 覆盖。
const DEFAULT_BACKEND = "http://127.0.0.1:8003";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget = env.VITE_BACKEND_TARGET || DEFAULT_BACKEND;
  const base = env.VITE_PUBLIC_PATH || "/";

  // 代理配置（dev 和 preview 共用）
  const proxyConfig = {
    "/api": {
      target: backendTarget,
      changeOrigin: true,
      rewrite: (p: string) => p.replace(/^\/api/, ""),
    },
    "/zfile": {
      target: env.VITE_ZFILE_TARGET || "http://127.0.0.1:8081",
      changeOrigin: true,
      rewrite: (p: string) => p.replace(/^\/zfile/, ""),
    },
    "/alist": {
      target: env.VITE_ALIST_TARGET || "http://127.0.0.1:5244",
      changeOrigin: true,
      rewrite: (p: string) => p.replace(/^\/alist/, ""),
    },
  };

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
      allowedHosts: true,
      proxy: proxyConfig,
    },
    // 生产预览：npm run build && npm run preview
    // 支持相同的环境变量覆盖代理目标（适配 Tailscale 跨网络场景）
    preview: {
      host: "0.0.0.0",
      port: 4173,
      allowedHosts: true,
      proxy: proxyConfig,
    },
    build: {
      outDir: "dist",
      sourcemap: mode !== "production",
      chunkSizeWarningLimit: 1024,
    },
  };
});
