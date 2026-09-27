"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Icon } from "@/components/ui/Icon";
import type { NavItem } from "./nav";

export function MobileMenu({
  locale,
  brand,
  items,
  secondary,
  openLabel,
  closeLabel,
  appUrl,
  loginLabel,
  startLabel,
}: {
  locale: string;
  brand: string;
  items: NavItem[];
  secondary: { label: string; path: string }[];
  openLabel: string;
  closeLabel: string;
  appUrl: string;
  loginLabel: string;
  startLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  const [seenPath, setSeenPath] = useState(pathname);
  if (seenPath !== pathname) {
    setSeenPath(pathname);
    setOpen(false);
  }

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      document.body.style.overflow = "hidden";
    } else if (!open && dialog.open) {
      dialog.close();
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  const base = `/${locale}`;

  return (
    <>
      <button
        type="button"
        className="inline-grid size-11 place-items-center rounded-full border border-border-2 bg-surface text-ink transition-colors hover:bg-surface-2 lg:hidden"
        aria-label={open ? closeLabel : openLabel}
        aria-expanded={open}
        aria-controls="mobile-menu"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name={open ? "x" : "menu"} size={22} />
      </button>

      <dialog
        id="mobile-menu"
        ref={dialogRef}
        aria-labelledby={titleId}
        className="sheet lg:hidden"
        onClose={() => setOpen(false)}
        onClick={(e) => {
          if (e.target === e.currentTarget) setOpen(false);
        }}
      >
        <div className="sheet-body">
          <div className="mb-3 flex items-center justify-between">
            <p id={titleId} className="mono text-xs font-semibold text-ink-3">
              {brand}
            </p>
            <button type="button" className="inline-grid size-11 place-items-center rounded-full hover:bg-surface-2" aria-label={closeLabel} onClick={() => setOpen(false)}>
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
                      active ? "border-amber/50 bg-amber/10 text-amber-text" : "border-border-2 bg-bg-2 text-ink"
                    }`}
                  >
                    <Icon name={item.icon} size={20} />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <a href={appUrl} className="btn btn-ghost">
              {loginLabel}
            </a>
            <a href={appUrl} className="btn btn-primary">
              {startLabel}
            </a>
          </div>
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-border-2 pt-3 text-sm text-ink-2">
            {secondary.map((s) => (
              <li key={s.path}>
                <Link href={`${base}${s.path}`} onClick={() => setOpen(false)} className="inline-flex min-h-11 items-center hover:text-ink">
                  {s.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </dialog>
    </>
  );
}
