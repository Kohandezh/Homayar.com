import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { toast } from "@heroui/react";
import { afterEach, vi } from "vitest";

// jsdom has no matchMedia; HeroUI reads it for its responsive components.
// Every query answers "no match", which is the desktop layout.
Object.defineProperty(window, "matchMedia", {
  configurable: true,
  writable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(() => false),
  }),
});

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

// jsdom measures nothing, so the observer never has a size to report.
// HeroUI's toast region and table only need the constructor to exist.
class NoopResizeObserver implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = NoopResizeObserver;
globalThis.ResizeObserver = NoopResizeObserver;

afterEach(() => {
  if (!hasDom) return;
  cleanup();
  // The toast queue is a module singleton, so one test's toast would
  // otherwise still be on screen in the next one.
  toast.clear();
  window.localStorage.clear();
});
