"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Logo } from "@/components/logo";
import { CartIcon, CloseIcon, MenuIcon } from "@/components/icons";
import { NAV_LINKS } from "@/lib/site";
import { useCart } from "@/lib/cart";

function CartBadge() {
  const { count, ready } = useCart();
  return (
    <Link
      href="/cart"
      aria-label={`Cart, ${count} item${count === 1 ? "" : "s"}`}
      className="relative flex h-10 w-10 items-center justify-center rounded-lg border border-line text-ink-soft transition-colors hover:border-line-strong hover:text-ink"
    >
      <CartIcon width={19} height={19} />
      {ready && count > 0 && (
        <span className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-accent px-1 text-[11px] font-bold text-bg">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Link>
  );
}

export function Header() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <header className="sticky top-0 z-50 border-b border-line bg-bg/85 backdrop-blur-md">
      <div className="container-site flex h-16 items-center justify-between gap-4">
        <Logo />

        <nav aria-label="Main" className="hidden lg:block">
          <ul className="flex items-center gap-1">
            {NAV_LINKS.map((link) => {
              const active =
                pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    aria-current={active ? "page" : undefined}
                    className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                      active
                        ? "bg-raised text-ink"
                        : "text-ink-muted hover:bg-raised/60 hover:text-ink"
                    }`}
                  >
                    {link.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="flex items-center gap-2.5">
          <CartBadge />
          <Link
            href="/contact"
            className="hidden h-10 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-bg transition-colors hover:bg-accent-strong sm:flex"
          >
            Book a demo
          </Link>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-label={open ? "Close menu" : "Open menu"}
            className="flex h-10 w-10 items-center justify-center rounded-lg border border-line text-ink-soft hover:text-ink lg:hidden"
          >
            {open ? (
              <CloseIcon width={20} height={20} />
            ) : (
              <MenuIcon width={20} height={20} />
            )}
          </button>
        </div>
      </div>

      {open && (
        <nav
          aria-label="Mobile"
          className="border-t border-line bg-bg lg:hidden"
        >
          <ul className="container-site flex flex-col gap-1 py-4">
            {NAV_LINKS.map((link) => {
              const active =
                pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    aria-current={active ? "page" : undefined}
                    className={`block rounded-lg px-3 py-3 text-base font-medium ${
                      active
                        ? "bg-raised text-ink"
                        : "text-ink-soft hover:bg-raised/60 hover:text-ink"
                    }`}
                  >
                    {link.label}
                  </Link>
                </li>
              );
            })}
            <li className="mt-2">
              <Link
                href="/contact"
                className="flex h-11 items-center justify-center rounded-lg bg-accent px-4 text-sm font-semibold text-bg"
              >
                Book a demo
              </Link>
            </li>
          </ul>
        </nav>
      )}
    </header>
  );
}
