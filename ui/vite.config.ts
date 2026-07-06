import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const DEFAULT_UI_HOST = "127.0.0.1";
const DEFAULT_UI_PORT = 4173;
const DEFAULT_API_PROXY_TARGET = "http://127.0.0.1:5000";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    plugins: [react()],
    server: {
      host: env.VITE_HOST || DEFAULT_UI_HOST,
      port: Number(env.VITE_PORT || DEFAULT_UI_PORT),
      strictPort: true,
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY_TARGET || DEFAULT_API_PROXY_TARGET,
          changeOrigin: true,
        },
      },
    },
  };
});
