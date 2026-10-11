import { promises as fs } from "node:fs";
import path from "node:path";
import type { LockPort } from "./types";

const STALE_AFTER_MS = 6 * 60 * 60 * 1000;
const SAFE_KEY = /^[A-Za-z0-9._-]+$/;

interface LockInfo {
  pid: number;
  createdAt: string;
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    // EPERM means the process exists but belongs to another user.
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

async function isStale(file: string): Promise<boolean> {
  try {
    const info = JSON.parse(await fs.readFile(file, "utf8")) as Partial<LockInfo>;
    const createdAt = Date.parse(info.createdAt ?? "");
    if (typeof info.pid !== "number" || Number.isNaN(createdAt)) return false;
    return Date.now() - createdAt > STALE_AFTER_MS && !isProcessAlive(info.pid);
  } catch {
    return false;
  }
}

/**
 * One lock file per key: `<dir>/<key>.lock`, created with an exclusive create so two workers
 * cannot both get it. A lock older than 6 hours whose process is gone is taken over.
 */
export function createFileLock(dir: string): LockPort {
  const fileFor = (key: string): string => {
    if (!SAFE_KEY.test(key)) throw new Error(`Invalid lock key "${key}".`);
    return path.join(dir, `${key}.lock`);
  };

  const create = async (file: string): Promise<boolean> => {
    const info: LockInfo = { pid: process.pid, createdAt: new Date().toISOString() };
    try {
      await fs.writeFile(file, JSON.stringify(info), { flag: "wx" });
      return true;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EEXIST") return false;
      throw error;
    }
  };

  return {
    async tryAcquire(key) {
      const file = fileFor(key);
      await fs.mkdir(dir, { recursive: true });
      if (await create(file)) return true;
      if (!(await isStale(file))) return false;
      await fs.rm(file, { force: true });
      return create(file);
    },
    async release(key) {
      await fs.rm(fileFor(key), { force: true });
    },
  };
}
