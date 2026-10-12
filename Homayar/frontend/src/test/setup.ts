import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// The component tests run in jsdom. `e2e/smtp-sink.test.ts` tests plain Node
// code and runs in the `node` environment, where there is no DOM to prepare.
const hasDom = typeof window !== "undefined";

const storageValues = new Map<string, string>();
if (hasDom) {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => storageValues.get(key) ?? null,
      setItem: (key: string, value: string) => storageValues.set(key, String(value)),
      removeItem: (key: string) => storageValues.delete(key),
      clear: () => storageValues.clear(),
      key: (index: number) => [...storageValues.keys()][index] ?? null,
      get length() {
        return storageValues.size;
      },
    } satisfies Storage,
  });
}

afterEach(() => {
  if (!hasDom) return;
  cleanup();
  window.localStorage.clear();
});
