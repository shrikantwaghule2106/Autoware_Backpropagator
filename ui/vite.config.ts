import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands inside the Python package and is committed, so users need no Node.js.
// FastAPI serves "/" (index.html) and "/static/…" (everything else) — hence base "/static/".
export default defineConfig({
  plugins: [react()],
  base: "/static/",
  build: {
    outDir: "../src/adviewer/ui/static",
    emptyOutDir: true,
    assetsDir: "assets",
    chunkSizeWarningLimit: 900,
  },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } }, // `npm run dev` next to `adviewer serve`
});
