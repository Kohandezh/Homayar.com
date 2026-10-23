import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    // The reviewer's acceptance tests run with their own config (tests/oracle/vitest.oracle.config.ts).
    exclude: ["tests/oracle/**", "node_modules/**"],
  },
});
