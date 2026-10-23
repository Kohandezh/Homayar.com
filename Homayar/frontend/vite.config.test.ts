import { describe, expect, it, vi } from "vitest";

import { consoleBuildValue } from "./vite.config";

/**
 * The rules behind the `__CONSOLE_BUILD__` constant (HOM-232 V2), exported
 * from vite.config.ts and shared with vitest.config.ts. Git is injected as
 * a function, so these tests never run it by accident. The file sits at the
 * frontend root (outside the tsconfig projects), but the rules themselves
 * are type-checked with vite.config.ts in tsconfig.node.json.
 */
describe("consoleBuildValue", () => {
  it("cuts a full 40-character env SHA to 7 characters without running git", () => {
    const gitShortSha = vi.fn(() => "aaaaaaa");
    const value = consoleBuildValue({
      env: { VITE_SOURCE_SHA: "0123456789abcdef".repeat(3) },
      packageVersion: "0.5.0",
      gitShortSha,
    });
    expect(value.sha).toBe("0123456");
    expect(gitShortSha).not.toHaveBeenCalled();
  });

  it("treats an all-zero env SHA as no SHA, without running git", () => {
    const gitShortSha = vi.fn(() => "aaaaaaa");
    const value = consoleBuildValue({
      env: { VITE_SOURCE_SHA: "0".repeat(40) },
      packageVersion: "0.5.0",
      gitShortSha,
    });
    expect(value.sha).toBeNull();
    expect(gitShortSha).not.toHaveBeenCalled();
  });

  it("runs git only when the env SHA is not set", () => {
    expect(consoleBuildValue({ env: {}, packageVersion: "0.5.0", gitShortSha: () => "abc1234def" }).sha).toBe("abc1234");
    expect(consoleBuildValue({ env: {}, packageVersion: "0.5.0", gitShortSha: () => null }).sha).toBeNull();

    // A blank env value counts as not set.
    const gitShortSha = vi.fn(() => "abc1234");
    expect(consoleBuildValue({ env: { VITE_SOURCE_SHA: "   " }, packageVersion: "0.5.0", gitShortSha }).sha).toBe("abc1234");
    expect(gitShortSha).toHaveBeenCalled();
  });

  it("prefers the env version over the package version", () => {
    expect(consoleBuildValue({ env: { VITE_RELEASE_VERSION: "9.9.9" }, packageVersion: "0.5.0", gitShortSha: () => null }).version).toBe("9.9.9");
    expect(consoleBuildValue({ env: {}, packageVersion: "0.5.0", gitShortSha: () => null }).version).toBe("0.5.0");
  });
});
