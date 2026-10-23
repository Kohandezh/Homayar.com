import { describe, expect, it } from "vitest";

import { CONSOLE_BUILD } from "@/lib/build-info";

/**
 * V1/V2 of HOM-232: `src/lib/build-info.ts` reads the `__CONSOLE_BUILD__`
 * constant that `define` injects in both `vite.config.ts` and
 * `vitest.config.ts`. This file runs under Vitest, so it sees the value the
 * Vitest config computed with no release environment: the package version
 * (0.5.0 since V3) and the worktree's short SHA, or null outside a checkout.
 */
describe("CONSOLE_BUILD", () => {
  it("is an object with a version string and a sha of string or null", () => {
    expect(typeof CONSOLE_BUILD).toBe("object");
    expect(CONSOLE_BUILD).not.toBeNull();
    expect(typeof CONSOLE_BUILD.version).toBe("string");
    expect(CONSOLE_BUILD.version.length).toBeGreaterThan(0);
    expect(CONSOLE_BUILD.sha === null || typeof CONSOLE_BUILD.sha === "string").toBe(true);
  });

  it("carries the package version the Vitest config reads from package.json", () => {
    // The product release version the backend reports in /healthz (V3).
    expect(CONSOLE_BUILD.version).toBe("0.5.0");
  });

  it("has a sha of exactly 7 lowercase hex characters, or null", () => {
    if (CONSOLE_BUILD.sha !== null) expect(CONSOLE_BUILD.sha).toMatch(/^[0-9a-f]{7}$/);
  });
});
