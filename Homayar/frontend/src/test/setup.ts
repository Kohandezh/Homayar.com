import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

const storageValues = new Map<string, string>();
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

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
