import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";
import { consoleBuildDefine } from "./vite.config";

const frontendDir = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  // The same console build identity vite.config.ts injects (HOM-232 V1/V2),
  // from the shared rules exported there, so `src/lib/build-info.ts` reads
  // a real constant under Vitest too.
  define: consoleBuildDefine({
    env: process.env,
    packageVersion: (JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as { version: string })
      .version,
    gitShortSha: () => {
      try {
        return (
          execSync("git rev-parse --short=7 HEAD", { cwd: frontendDir, stdio: ["ignore", "pipe", "ignore"] })
            .toString()
            .trim() || null
        );
      } catch {
        return null;
      }
    },
  }),
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
