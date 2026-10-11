import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const packageDir = fileURLToPath(new URL("..", import.meta.url));
const tsx = path.join(packageDir, "node_modules", ".bin", "tsx");

function runCli(args: string[], extraEnv: Record<string, string> = {}) {
  const env: NodeJS.ProcessEnv = { PATH: process.env.PATH, HOME: process.env.HOME, ...extraEnv };
  return spawnSync(tsx, ["src/cli.ts", ...args], { cwd: packageDir, env, encoding: "utf8", timeout: 60_000 });
}

describe("cli", () => {
  it("dry run without LINEAR_API_KEY exits non-zero with a clear message and no stack trace", () => {
    const result = runCli(["dry-run"]);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("LINEAR_API_KEY is missing");
    expect(result.stderr).not.toMatch(/\n\s+at /);
  });

  it("a normal run names every missing key", () => {
    const result = runCli(["once"], { LINEAR_API_KEY: "lin_test_key" });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("ANTHROPIC_API_KEY, GITHUB_TOKEN are missing");
  });

  it("an unknown mode prints the usage", () => {
    const result = runCli(["merge"]);
    expect(result.status).toBe(2);
    expect(result.stderr).toContain("Usage");
  });
});
