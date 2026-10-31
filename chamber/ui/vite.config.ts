import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built into ../board with fixed names: the relay hands these files out with the
// client, and `chamber board` serves them. In development the API is a running board.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { proxy: { "/api": "http://127.0.0.1:7878" } },
  build: {
    outDir: "../board",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: { output: { entryFileNames: "assets/board.mjs", chunkFileNames: "assets/[name].mjs", assetFileNames: "assets/board[extname]" } },
  },
});
