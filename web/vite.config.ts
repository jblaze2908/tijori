import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The backend serves the built app from the same origin, so dev and preview proxy to it.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "TIJORI_");
  const target = env.TIJORI_API_URL || "http://localhost:8310";
  // docs/api.md: with TIJORI_ENV=dev the API identifies the member by this header; prod ignores it.
  const headers = env.TIJORI_DEV_MEMBER ? { "X-Tijori-Dev-Member": env.TIJORI_DEV_MEMBER } : undefined;
  const rule = { target, headers };
  const proxy = { "/api": rule, "/auth": rule, "/health": rule };
  return {
    plugins: [react()],
    server: { proxy },
    preview: { proxy },
  };
});
