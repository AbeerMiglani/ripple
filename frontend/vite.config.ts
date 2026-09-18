import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

// Where the dev server proxies /api to. Defaults to the host-based workflow
// (`npm run dev` alongside `docker compose up`); the containerised demo sets
// VITE_API_TARGET=http://backend:8000 so the proxy reaches the Compose service.
const apiTarget = process.env.VITE_API_TARGET ?? "http://localhost:8000";
const wsTarget = apiTarget.replace(/^http/, "ws");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  optimizeDeps: {
    // maplibre-gl ships a separate worker bundle that it loads at runtime via
    // `new URL(..., import.meta.url)`. Vite's dep pre-bundler only sees the
    // main entry point, rewrites its import.meta.url, and never copies the
    // sibling worker file alongside it -- so the worker 404s in dev (fine in
    // `vite build`, which bundles it correctly). Excluding it makes Vite serve
    // the package straight from node_modules, where the relative path holds.
    exclude: ["maplibre-gl"],
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/api/ws": {
        target: wsTarget,
        ws: true,
      },
    },
  },
  test: {
    // Pure-logic coverage only (stores, derivations) — no jsdom/RTL yet, so
    // the default "node" environment is enough and keeps this fast.
    include: ["src/**/*.test.ts"],
  },
});
