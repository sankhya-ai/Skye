import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on Vite and the API on the Python host.
const api = process.env.TURA_CODE_API ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: api, ws: true, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
