/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

const stripTrailingWhitespace = {
  name: "strip-trailing-whitespace",
  renderChunk(code: string) {
    const clean = code.replace(/[ \t]+$/gm, "");
    return clean === code ? null : { code: clean, map: null };
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    // Component tests (ProviderBar etc.) need a DOM; plain logic tests
    // (providerScope.test.ts) run fine under it too.
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
  server: {
    port: 5173,
    // `npm run dev` serves the UI; the API stays on the Python process.
    proxy: { "/api": "http://127.0.0.1:8787" },
  },
  build: {
    // Build into the Python package, not web/dist: setuptools only ships
    // files under throughline/, so this is what makes `pip install
    // throughline` serve a UI without needing Node at install time.
    outDir: "../throughline/web",
    emptyOutDir: true,
    // Built assets are committed and shipped in the wheel, so keep the
    // output stable and reviewable rather than sprawling across chunks.
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    // Some dependency template literals contain whitespace-only lines. Keep
    // generated assets compatible with the repository-wide diff gate as part
    // of the reproducible build, before Rollup writes or hashes each chunk.
    rollupOptions: { output: { plugins: [stripTrailingWhitespace] } },
  },
});
