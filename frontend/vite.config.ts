import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // Node/Vite 개발 서버 내부에서만 읽는 URL. 브라우저 코드에 포함하지 않는다.
  const env = loadEnv(mode, ".", "");
  const backendUrl = env.MAPGPT_BACKEND_URL || "http://124.5.160.24:8000";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: backendUrl,
          changeOrigin: true,
          timeout: 300_000,
          proxyTimeout: 300_000,
        },
      },
    },
  };
});
