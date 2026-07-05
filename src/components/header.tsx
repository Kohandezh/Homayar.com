"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Logo } from "@/components/logo";
import { LangSwitcher } from "@/components/lang-switcher";
import { CartIcon, CloseIcon, MenuIcon } from "@/components/icons";
import { navLinks } from "@/lib/site-i18n";
import { CONTENT } from "@/lib/content";
import { useLocale } from "@/lib/locale";
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
  const { locale } = useLocale();
  const [open, setOpen] = useState(false);

  const links = navLinks(locale);
  const demoLabel = CONTENT[locale].cta.demo;

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
    <header className="sticky top-0 z-50 border-b border-line/70 bg-bg/70 backdrop-blur-xl backdrop-saturate-150">
      <div className="container-site flex h-16 items-center justify-between gap-4">
        <Logo />

        <nav aria-label="Main" className="hidden lg:block">
          <ul className="flex items-center gap-1">
            {links.map((link) => {
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
          <div className="hidden sm:block">
            <LangSwitcher />
          </div>
          <Link
            href="/contact"
            className="hidden h-10 items-center rounded-lg bg-accent px-4 text-sm font-semibold text-bg transition-all duration-300 hover:bg-accent-strong hover:cta-glow hover:-translate-y-0.5 sm:flex"
          >
            {demoLabel}
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
          className="glass-strong border-t border-line lg:hidden"
        >
          <ul className="container-site flex flex-col gap-1 py-4">
            {links.map((link) => {
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
            <li className="mt-2 flex items-center gap-2.5">
              <LangSwitcher />
              <Link
                href="/contact"
                className="flex h-11 flex-1 items-center justify-center rounded-lg bg-accent px-4 text-sm font-semibold text-bg"
              >
                {demoLabel}
              </Link>
            </li>
          </ul>
        </nav>
      )}
    </header>
  );
}
