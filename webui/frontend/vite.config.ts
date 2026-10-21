// Builds the app into ../usas_web/static/app, which FastAPI serves (main.py reads .vite/manifest.json to link
// the entry script and stylesheet). The output must suit the server's Content-Security-Policy: external files
// only, no inline scripts or styles and no data: URLs, so small assets are never inlined.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  base: "/static/app/",
  build: {
    outDir: "../usas_web/static/app",
    emptyOutDir: true,
    manifest: true,
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    sourcemap: false,
    rollupOptions: { input: "src/main.tsx" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["test/setup.ts"],
    // CSS is ignored in tests, except the tokens, which test/contrast.test.ts reads.
    css: { include: [/tokens\.css/] },
    include: ["test/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**"],
      exclude: ["src/main.tsx", "src/**/*.css"],
      reporter: ["text-summary", "text"],
      thresholds: { statements: 80, branches: 75, functions: 80, lines: 80 },
    },
  },
});
