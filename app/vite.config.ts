import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: run `python -m textbook2md serve --port 8765` with T2MD_DEV=1 and `npm run dev`.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", sourcemap: false },
});
