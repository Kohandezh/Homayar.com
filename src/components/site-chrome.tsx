"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Header } from "@/components/header";
import { Footer } from "@/components/footer";

/**
 * Shared site chrome (responsive header + footer) for every page EXCEPT the
 * homepage, which ships its own bespoke cinematic header/footer inside
 * <Landing/>. Without this wrapper, inner pages (pricing, catalog, platform…)
 * rendered with no navigation at all — a dead end on mobile, where there was no
 * menu, logo home-link, or footer to move around the site. usePathname() lets a
 * single client wrapper opt the homepage out instead of restructuring every
 * route into a layout group.
 */
export function SiteChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  // Homepage owns its own header/footer — render it untouched.
  if (pathname === "/") return <>{children}</>;

  return (
    <>
      <Header />
      <main className="flex-1">{children}</main>
      <Footer />
    </>
  );
}
