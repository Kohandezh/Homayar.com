import os from "node:os";
import { describe, expect, it } from "vitest";
import { execCommand } from "../src/exec";

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function waitUntilDead(pid: number, ms: number): Promise<boolean> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (!isAlive(pid)) return true;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return !isAlive(pid);
}

describe("execCommand", () => {
  it("returns the exit code and the output in order", async () => {
    const result = await execCommand(process.execPath, ["-e", "console.log('a'); console.error('b'); process.exit(3)"], {
      cwd: os.tmpdir(),
    });
    expect(result.code).toBe(3);
    expect(result.stdout.trim()).toBe("a");
    expect(result.stderr.trim()).toBe("b");
  });

  it("a timeout stops the whole process group, not only the direct child", async () => {
    // The child starts a grandchild that never ends, prints its pid, then waits forever too.
    const script = [
      "const { spawn } = require('node:child_process');",
      "const grandchild = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });",
      "console.log(grandchild.pid);",
      "setInterval(() => {}, 1000);",
    ].join("\n");
    const started = Date.now();
    const result = await execCommand(process.execPath, ["-e", script], { cwd: os.tmpdir(), timeoutMs: 1000 });
    const grandchildPid = Number(result.stdout.trim().split("\n")[0]);
    expect(Number.isInteger(grandchildPid) && grandchildPid > 0).toBe(true);
    expect(result.output).toContain("stopped after 1000 ms");
    expect(Date.now() - started).toBeLessThan(9000);
    expect(await waitUntilDead(grandchildPid, 3000)).toBe(true);
  });
});
