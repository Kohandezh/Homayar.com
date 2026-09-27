"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Icon } from "@/components/ui/Icon";
import type { NavItem } from "./nav";

/** Menu button plus a bottom sheet with every page. Used below the desktop breakpoint. */
export function MobileMenu({
  locale,
  items,
  secondary,
  openLabel,
  closeLabel,
  appUrl,
  loginLabel,
}: {
  locale: string;
  items: NavItem[];
  secondary: { label: string; path: string }[];
  openLabel: string;
  closeLabel: string;
  appUrl: string;
  loginLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const dialogRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  // Close on navigation: when the path changes, reset the open state during render.
  const [seenPath, setSeenPath] = useState(pathname);
  if (seenPath !== pathname) {
    setSeenPath(pathname);
    setOpen(false);
  }

  // Escape closes; focus moves into the sheet when it opens and back to the button when it closes.
  useEffect(() => {
    if (!open) return;
    const button = buttonRef.current;
    const previous = document.activeElement as HTMLElement | null;
    const first = dialogRef.current?.querySelector<HTMLElement>("a, button");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      (previous ?? button)?.focus();
    };
  }, [open]);

  const base = `/${locale}`;

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="inline-grid size-11 place-items-center rounded-full border border-border bg-surface text-ink transition-colors hover:bg-sand lg:hidden"
        aria-label={open ? closeLabel : openLabel}
        aria-expanded={open}
        aria-controls="mobile-menu"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name={open ? "x" : "menu"} size={22} />
      </button>

      {open ? (
        <div className="fixed inset-0 z-50 lg:hidden" role="presentation">
          <button
            type="button"
            className="absolute inset-0 bg-ink/40"
            aria-label={closeLabel}
            onClick={() => setOpen(false)}
            tabIndex={-1}
          />
          <div
            id="mobile-menu"
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            className="sheet absolute inset-x-0 bottom-0 max-h-[85dvh] overflow-y-auto rounded-t-[16px] bg-surface p-4 pb-[calc(1rem+env(safe-area-inset-bottom,0px))] shadow-2xl"
          >
            <div className="mb-3 flex items-center justify-between">
              <p id={titleId} className="text-sm font-semibold text-ink-2">
                Homayar
              </p>
              <button
                type="button"
                className="inline-grid size-11 place-items-center rounded-full hover:bg-sand"
                aria-label={closeLabel}
                onClick={() => setOpen(false)}
              >
                <Icon name="x" size={22} />
              </button>
            </div>
            <ul className="grid grid-cols-2 gap-2">
              {items.map((item) => {
                const href = `${base}${item.path}`;
                const active = pathname === href || pathname.startsWith(`${href}/`);
                return (
                  <li key={item.key}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      onClick={() => setOpen(false)}
                      className={`flex min-h-14 items-center gap-3 rounded-xl border px-3 py-2 font-semibold ${
                        active ? "border-primary bg-primary-soft text-primary" : "border-border bg-bg text-ink"
                      }`}
                    >
                      <Icon name={item.icon} size={20} />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
            <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-2 border-t border-border pt-3 text-sm text-ink-2">
              {secondary.map((s) => (
                <li key={s.path}>
                  <Link href={`${base}${s.path}`} onClick={() => setOpen(false)} className="inline-flex min-h-11 items-center hover:text-ink">
                    {s.label}
                  </Link>
                </li>
              ))}
              <li>
                <a href={appUrl} className="inline-flex min-h-11 items-center font-semibold text-primary">
                  {loginLabel}
                </a>
              </li>
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}
