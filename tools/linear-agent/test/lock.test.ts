import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createFileLock } from "../src/index";

describe("createFileLock", () => {
  let base = "";
  let dir = "";
  beforeEach(() => {
    base = mkdtempSync(path.join(os.tmpdir(), "linear-agent-lock-"));
    dir = path.join(base, "locks");
  });
  afterEach(() => rmSync(base, { recursive: true, force: true }));

  it("creates the folder and the lock file, and a second acquire fails until release", async () => {
    const lock = createFileLock(dir);
    expect(await lock.tryAcquire("HOM-1")).toBe(true);
    expect(JSON.parse(readFileSync(path.join(dir, "HOM-1.lock"), "utf8"))).toMatchObject({ pid: process.pid });
    expect(await lock.tryAcquire("HOM-1")).toBe(false);
    await lock.release("HOM-1");
    expect(await lock.tryAcquire("HOM-1")).toBe(true);
  });

  it("another lock object on the same folder sees the lock; a different key is independent", async () => {
    const first = createFileLock(dir);
    const second = createFileLock(dir);
    expect(await first.tryAcquire("HOM-1")).toBe(true);
    expect(await second.tryAcquire("HOM-1")).toBe(false);
    expect(await second.tryAcquire("HOM-2")).toBe(true);
  });

  it("only one of many parallel acquires wins", async () => {
    const results = await Promise.all(Array.from({ length: 10 }, () => createFileLock(dir).tryAcquire("HOM-3")));
    expect(results.filter(Boolean)).toHaveLength(1);
  });

  it("releasing a key that is not held does not throw", async () => {
    await expect(createFileLock(dir).release("HOM-9")).resolves.toBeUndefined();
  });

  it("takes over a lock older than 6 hours whose process is gone, but not a fresh one", async () => {
    const lock = createFileLock(dir);
    await lock.tryAcquire("HOM-4");
    const old = new Date(Date.now() - 7 * 60 * 60 * 1000).toISOString();
    writeFileSync(path.join(dir, "HOM-4.lock"), JSON.stringify({ pid: 999_999_999, createdAt: old }));
    expect(await lock.tryAcquire("HOM-4")).toBe(true);

    writeFileSync(path.join(dir, "HOM-5.lock"), JSON.stringify({ pid: 999_999_999, createdAt: new Date().toISOString() }));
    expect(await lock.tryAcquire("HOM-5")).toBe(false);
    writeFileSync(path.join(dir, "HOM-6.lock"), JSON.stringify({ pid: process.pid, createdAt: old }));
    expect(await lock.tryAcquire("HOM-6")).toBe(false);
  });

  it("rejects keys that could leave the folder", async () => {
    await expect(createFileLock(dir).tryAcquire("../HOM-1")).rejects.toThrow("Invalid lock key");
  });
});
