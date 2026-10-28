import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build straight into the Python package: umbra serve ships what lands here
// (ADR 0001 — users never need Node; the output is committed, see the plan).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Relative asset paths: the same build is served by umbra serve at / and,
  // exported, from wherever the person's host puts it (umbra export).
  base: "./",
  build: { outDir: "../umbra/web", emptyOutDir: true },
  server: {
    // `umbra serve --api-only` on its default port; the page comes from Vite.
    proxy: { "/api": "http://127.0.0.1:5170" },
  },
});
