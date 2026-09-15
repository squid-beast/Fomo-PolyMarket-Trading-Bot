import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds into ../static, which FastAPI serves. In dev, /api is proxied to the
// running daemon UI server so the SSE stream works without CORS.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../static", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
        // an event stream must not be buffered by the dev proxy
        configure: (proxy) => {
          proxy.on("proxyRes", (res) => { res.headers["x-accel-buffering"] = "no"; });
        },
      },
    },
  },
});
