import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { SandboxManager } from "@anthropic-ai/sandbox-runtime";
import { afterAll, describe, expect, it } from "vitest";
import { checkSandboxPolicy, createCommandChecks, createSrtCheckSandbox, shellQuote } from "../src/index";
import type { CheckSandboxPolicy } from "../src/index";
import type { SandboxManagerLike } from "../src/check-sandbox";
import { testConfig } from "./fakes";

function fakeManager(overrides: Partial<{ supported: boolean; errors: string[] }> = {}) {
  const calls: { method: string; args: unknown[] }[] = [];
  const manager: SandboxManagerLike = {
    isSupportedPlatform: () => overrides.supported ?? true,
    checkDependencies: () => ({ errors: overrides.errors ?? [], warnings: [] }),
    initialize: async (...args: unknown[]) => {
      calls.push({ method: "initialize", args });
    },
    wrapWithSandboxArgv: async (...args: unknown[]) => {
      calls.push({ method: "wrap", args });
      return { argv: ["/bin/sh", "-c", `sandboxed ${String(args[0])}`], env: { PATH: "/bin" } };
    },
    reset: async () => {
      calls.push({ method: "reset", args: [] });
    },
  } as unknown as SandboxManagerLike;
  return { manager, calls };
}

const policy: CheckSandboxPolicy = {
  allowedDomains: ["registry.npmjs.org"],
  denyRead: ["/home/x/.ssh"],
  writeRoots: ["/tmp", "/home/x/.npm"],
};

describe("checkSandboxPolicy", () => {
  it("uses the agent hosts without api.github.com, denies the secrets, and allows temp and caches", () => {
    const result = checkSandboxPolicy(testConfig({ repoPath: "/repo" }), "/home/x", "/var/tmp/x");
    expect(result.allowedDomains).not.toContain("api.github.com");
    expect(result.allowedDomains).toEqual(expect.arrayContaining(["registry.npmjs.org", "pypi.org", "github.com", "api.linear.app"]));
    expect(result.denyRead).toEqual(expect.arrayContaining(["/repo/tools/linear-agent/.env", "/home/x/.ssh", "/home/x/.config/gh"]));
    expect(result.writeRoots).toEqual(expect.arrayContaining(["/var/tmp/x", "/tmp", "/home/x/.npm", "/home/x/.cache/uv"]));
    expect(result.writeRoots).not.toContain("/home/x");
  });
});

describe("shellQuote", () => {
  it.each([
    ["npm", "npm"],
    ["backend/tests/test_a.py", "backend/tests/test_a.py"],
    ["--frozen-lockfile", "--frozen-lockfile"],
    ["test a.py", "'test a.py'"],
    ["x;rm -rf ~", "'x;rm -rf ~'"],
    ["it's", "'it'\\''s'"],
    ["$(id)", "'$(id)'"],
  ])("%s becomes %s", (input, output) => {
    expect(shellQuote(input)).toBe(output);
  });
});

describe("createSrtCheckSandbox with a fake manager", () => {
  it("starts once, then wraps each command with writes limited to the worktree", async () => {
    const { manager, calls } = fakeManager();
    const sandbox = createSrtCheckSandbox(policy, manager);
    const first = await sandbox.wrap(["uv", "run", "pytest", "backend/tests/test x.py"], "/wt/HOM-1", "/wt/HOM-1/Homayar");
    await sandbox.wrap(["npm", "ci"], "/wt/HOM-1", "/wt/HOM-1");

    expect(calls.filter((c) => c.method === "initialize")).toHaveLength(1);
    expect(calls[0].args[0]).toEqual({
      network: { allowedDomains: ["registry.npmjs.org"], deniedDomains: [] },
      filesystem: { denyRead: ["/home/x/.ssh"], allowWrite: [], denyWrite: [] },
    });
    const wrap = calls[1].args;
    expect(wrap[0]).toBe("uv run pytest 'backend/tests/test x.py'");
    expect(wrap[2]).toEqual({
      filesystem: {
        denyRead: ["/home/x/.ssh"],
        allowWrite: ["/wt/HOM-1", "/tmp", "/home/x/.npm"],
        denyWrite: ["/wt/HOM-1/.git"],
      },
    });
    expect(wrap[4]).toBe("/wt/HOM-1/Homayar");
    expect(first).toEqual({ argv: ["/bin/sh", "-c", "sandboxed uv run pytest 'backend/tests/test x.py'"], env: { PATH: "/bin" } });
  });

  it("throws a clear reason when the platform is not supported or a dependency is missing", async () => {
    await expect(createSrtCheckSandbox(policy, fakeManager({ supported: false }).manager).wrap(["npm"], "/wt/A", "/wt/A")).rejects.toThrow(
      "does not support this platform",
    );
    await expect(
      createSrtCheckSandbox(policy, fakeManager({ errors: ["bubblewrap (bwrap) not installed"] }).manager).wrap(["npm"], "/wt/A", "/wt/A"),
    ).rejects.toThrow("sandbox dependencies are missing: bubblewrap (bwrap) not installed");
  });

  it("tries to start again after a failed start, and close resets the manager", async () => {
    let fail = true;
    const { manager, calls } = fakeManager();
    const initialize = manager.initialize;
    manager.initialize = async (...args) => {
      if (fail) throw new Error("proxy failed");
      return initialize(...args);
    };
    const sandbox = createSrtCheckSandbox(policy, manager);
    await expect(sandbox.wrap(["npm"], "/wt/A", "/wt/A")).rejects.toThrow("proxy failed");
    fail = false;
    await sandbox.wrap(["npm"], "/wt/A", "/wt/A");
    await sandbox.close();
    expect(calls.map((c) => c.method)).toEqual(["initialize", "wrap", "reset"]);
  });
});

// The real sandbox (sandbox-exec on macOS, bubblewrap on Linux). Runs where it is available,
// for example on the developer Mac; skipped on a CI runner without bubblewrap.
const sandboxAvailable =
  SandboxManager.isSupportedPlatform() && SandboxManager.checkDependencies().errors.length === 0 && process.platform === "darwin";

describe.runIf(sandboxAvailable)("createSrtCheckSandbox on this machine", () => {
  const base = realpathSync(mkdtempSync(path.join(os.tmpdir(), "linear-agent-srt-")));
  const worktree = path.join(base, "worktree");
  const outside = path.join(base, "outside");
  const secret = path.join(base, "secret.env");
  writeFileSync(secret, "LINEAR_API_KEY=lin_test_key\n");
  const sandbox = createSrtCheckSandbox({ allowedDomains: ["registry.npmjs.org"], denyRead: [secret], writeRoots: [] });
  const checks = createCommandChecks({ sandbox });
  const node = process.execPath;
  const write = (target: string) => `${node} -e require("fs").writeFileSync(process.argv[1],"x") ${target}`;

  afterAll(async () => {
    await sandbox.close();
    rmSync(base, { recursive: true, force: true });
  });

  it("allows writes in the worktree, denies writes outside it and reads of a denied file", async () => {
    mkdirSync(worktree, { recursive: true });
    mkdirSync(outside, { recursive: true });
    const results = await checks.run(worktree, [{ name: "inside", cwd: ".", command: write(path.join(worktree, "ok.txt")) }]);
    expect(results[0].ok).toBe(true);
    expect(existsSync(path.join(worktree, "ok.txt"))).toBe(true);

    const blocked = await checks.run(worktree, [{ name: "outside", cwd: ".", command: write(path.join(outside, "no.txt")) }]);
    expect(blocked[0].ok).toBe(false);
    expect(existsSync(path.join(outside, "no.txt"))).toBe(false);

    const read = await checks.run(worktree, [
      { name: "read secret", cwd: ".", command: `${node} -e require("fs").readFileSync(process.argv[1]) ${secret}` },
    ]);
    expect(read[0].ok).toBe(false);
    expect(readFileSync(secret, "utf8")).toContain("lin_test_key");

    const pointer = await checks.run(worktree, [{ name: "git pointer", cwd: ".", command: write(path.join(worktree, ".git")) }]);
    expect(pointer[0].ok).toBe(false);
  });
});
