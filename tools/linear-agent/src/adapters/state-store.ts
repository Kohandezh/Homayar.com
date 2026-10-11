import { promises as fs } from "node:fs";
import path from "node:path";
import type { StateStore, TrackedPr } from "../types";

/**
 * Tracked PRs in one JSON file. A missing file means an empty list. A broken file throws,
 * because silently starting over would lose the PRs that still need follow-ups.
 */
export function createJsonStateStore(file: string): StateStore {
  return {
    async load() {
      let text: string;
      try {
        text = await fs.readFile(file, "utf8");
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
        throw error;
      }
      const data: unknown = JSON.parse(text);
      if (!Array.isArray(data)) throw new Error(`${file} must hold a JSON array of tracked PRs.`);
      return data as TrackedPr[];
    },
    async save(items) {
      await fs.mkdir(path.dirname(file), { recursive: true });
      // Write then rename, so a crash never leaves a half-written file.
      const tmp = `${file}.${process.pid}.tmp`;
      await fs.writeFile(tmp, `${JSON.stringify(items, null, 2)}\n`);
      await fs.rename(tmp, file);
    },
  };
}
