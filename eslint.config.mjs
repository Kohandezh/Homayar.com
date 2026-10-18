import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Product source (ADR 0033); it carries its own lint configuration and
    // is linted by the console CI job.
    "Homayar/**",
    // Marketing website; it carries its own lint configuration.
    "website/**",
    // Standalone tools; each carries its own lint configuration and CI job.
    "tools/**",
    // Claude Code skills; their helper scripts are not app code.
    ".claude/**",
  ]),
]);

export default eslintConfig;
