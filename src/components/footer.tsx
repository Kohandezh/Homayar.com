"use client";

import Link from "next/link";
import { FullLogo } from "@/components/logo";
import { SITE } from "@/lib/site";
import { footerGroups } from "@/lib/site-i18n";
import { CONTENT } from "@/lib/content";
import { useLocale } from "@/lib/locale";

export function Footer() {
  const { locale } = useLocale();
  const t = CONTENT[locale].footer;
  const groups = footerGroups(locale);

  return (
    <footer className="mt-auto border-t border-line bg-surface">
      <div className="container-site grid gap-10 py-14 md:grid-cols-[1.4fr_repeat(3,1fr)]">
        <div className="max-w-xs">
          <FullLogo />
          <p className="mt-4 text-sm font-semibold text-ink-soft">{t.tagline}</p>
          <p className="mt-3 text-sm leading-relaxed text-ink-muted">
            {t.description}
          </p>
        </div>

        {groups.map((group) => (
          <nav key={group.title} aria-label={group.title}>
            <h3 className="text-sm font-semibold text-ink">{group.title}</h3>
            <ul className="mt-4 space-y-2.5">
              {group.links.map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className="text-sm text-ink-muted transition-colors hover:text-ink"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>

      <div className="border-t border-line">
        <div className="container-site flex flex-col gap-2 py-6 text-xs text-ink-faint sm:flex-row sm:items-center sm:justify-between">
          <p>{t.copyright}</p>
          <a href={`mailto:${SITE.contactEmail}`} className="hover:text-ink-muted">
            {SITE.contactEmail}
          </a>
        </div>
      </div>
    </footer>
  );
}
