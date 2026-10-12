import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: false,
    // `e2e/*.spec.ts` are Playwright flows; `npm run test:e2e` runs those.
    // The sink's own unit tests (`e2e/*.test.ts`) do belong here.
    exclude: [...configDefaults.exclude, "e2e/**/*.spec.ts"],
  },
});
