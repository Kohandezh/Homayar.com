"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";
import type { Locale } from "@/lib/i18n";

export function PageMotion({ children, locale }: { children: ReactNode; locale: Locale }) {
  const pathname = usePathname();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root || !document.documentElement.classList.contains("motion")) return;
    let cleanup: (() => void) | undefined;
    let cancelled = false;
    import("./runtime")
      .then((m) => {
        if (cancelled) return;
        cleanup = m.setupPage(root, locale);
      })
      .catch(() => document.documentElement.classList.remove("motion"));
    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [pathname, locale]);

  return (
    <div key={pathname} ref={ref} data-page>
      {children}
    </div>
  );
}
