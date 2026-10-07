import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const apiTarget = env["VITE_API_PROXY_TARGET"] || "http://127.0.0.1:8000";

  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        "/api": apiTarget,
        "/healthz": apiTarget,
        "/readyz": apiTarget,
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      restoreMocks: true,
    },
  };
});
