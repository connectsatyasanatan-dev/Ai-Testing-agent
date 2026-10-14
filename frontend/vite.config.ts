import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // @/ resolves to src/ — keep in sync with tsconfig.json paths.
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5180,
    // Proxy API calls to the FastAPI backend — avoids CORS in local dev.
    proxy: {
      "/api":    { target: "http://localhost:8090", changeOrigin: true },
      "/health": { target: "http://localhost:8090", changeOrigin: true },
      "/ready":  { target: "http://localhost:8090", changeOrigin: true },
    },
  },
  // CSS Modules are handled automatically by Vite for *.module.css files.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});

