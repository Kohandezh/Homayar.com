"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";
import { applyTheme, readTheme, THEME_KEY, type Theme } from "@/lib/theme";

function subscribe(cb: () => void) {
  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_KEY && (e.newValue === "dark" || e.newValue === "light")) {
      document.documentElement.setAttribute("data-theme", e.newValue);
      window.dispatchEvent(new CustomEvent("themechange", { detail: e.newValue }));
      cb();
    }
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener("themechange", cb);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener("themechange", cb);
  };
}
const serverTheme = (): Theme => "dark";

export function ThemeToggle({ toDark, toLight, className = "" }: { toDark: string; toLight: string; className?: string }) {
  const theme = useSyncExternalStore(subscribe, readTheme, serverTheme);
  const next: Theme = theme === "dark" ? "light" : "dark";
  const label = theme === "dark" ? toLight : toDark;

  return (
    <button
      type="button"
      className={`icon-btn theme-toggle ${className}`}
      aria-label={label}
      title={label}
      aria-pressed={theme === "light"}
      data-testid="theme-toggle"
      onClick={() => applyTheme(next)}
    >
      <Sun className="theme-sun" size={20} strokeWidth={1.75} aria-hidden="true" />
      <Moon className="theme-moon" size={20} strokeWidth={1.75} aria-hidden="true" />
    </button>
  );
}
