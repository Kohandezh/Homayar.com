"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

/**
 * Re-mounts its child on every route change so the CSS enter animation runs.
 * The animation only touches opacity and transform, so nothing shifts.
 */
export function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div key={pathname} className="page-enter">
      {children}
    </div>
  );
}
