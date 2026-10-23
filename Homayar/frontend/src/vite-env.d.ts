/**
 * The console build identity (HOM-232 V1). `vite.config.ts` and
 * `vitest.config.ts` inject the constant through `define`, so no environment
 * variable or import is read at runtime.
 */
declare const __CONSOLE_BUILD__: {
  version: string;
  sha: string | null;
};
