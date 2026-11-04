import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
  build: { outDir: "../dist", emptyOutDir: true, rollupOptions: { output: { manualChunks: { vendor: ["react", "react-dom", "lucide-react"] } } } },
  server: { port: 5173 },
});
