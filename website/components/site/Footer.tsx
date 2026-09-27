import Link from "next/link";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { appUrl } from "@/lib/site";
import { Logo } from "./Logo";

export function Footer({ locale, dict }: { locale: Locale; dict: Dictionary }) {
  const base = `/${locale}`;
  const columns = [
    {
      title: dict.footer.product,
      links: [
        { label: dict.nav.product, href: `${base}/product` },
        { label: dict.nav.howItWorks, href: `${base}/how-it-works` },
        { label: dict.nav.solutions, href: `${base}/solutions` },
        { label: dict.nav.pricing, href: `${base}/pricing` },
      ],
    },
    {
      title: dict.footer.trust,
      links: [
        { label: dict.nav.security, href: `${base}/security` },
        { label: dict.nav.deployment, href: `${base}/deployment` },
        { label: dict.nav.transparency, href: `${base}/transparency` },
      ],
    },
    {
      title: dict.footer.company,
      links: [
        { label: dict.nav.about, href: `${base}/about` },
        { label: dict.nav.contact, href: `${base}/contact` },
        { label: dict.common.login, href: appUrl, external: true },
      ],
    },
    {
      title: dict.footer.legal,
      links: [
        { label: dict.nav.privacy, href: `${base}/legal/privacy` },
        { label: dict.nav.terms, href: `${base}/legal/terms` },
        { label: dict.nav.gdpr, href: `${base}/legal/gdpr` },
      ],
    },
  ];
  return (
    <footer className="relative isolate overflow-hidden border-t border-border bg-bg-2">
      <div aria-hidden="true" className="glow-teal pointer-events-none absolute -bottom-48 -start-32 -z-10 h-[420px] w-[520px]" />
      <div className="container-x grid grid-cols-2 gap-8 py-14 md:grid-cols-[1.5fr_repeat(4,1fr)] md:gap-10 md:py-20">
        <div className="col-span-2 flex flex-col gap-5 md:col-span-1">
          <Logo locale={locale} brand={dict.brand} tagline={dict.tagline} />
          <p className="max-w-sm text-sm text-ink-2">{dict.footer.note}</p>
        </div>
        {columns.map((col) => (
          <div key={col.title}>
            <h2 className="mono mb-3 text-xs font-semibold text-ink-3">{col.title}</h2>
            <ul className="flex flex-col gap-0.5">
              {col.links.map((l) =>
                "external" in l && l.external ? (
                  <li key={l.label}>
                    <a href={l.href} className="inline-flex min-h-9 items-center text-sm text-ink-2 hover:text-amber-text">
                      {l.label}
                    </a>
                  </li>
                ) : (
                  <li key={l.label}>
                    <Link href={l.href} className="inline-flex min-h-9 items-center text-sm text-ink-2 hover:text-amber-text">
                      {l.label}
                    </Link>
                  </li>
                ),
              )}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-border">
        <div className="container-x flex flex-wrap items-center justify-between gap-2 py-4 text-xs text-ink-3">
          <span>{dict.footer.rights}</span>
          <span className="mono">{dict.tagline}</span>
        </div>
      </div>
    </footer>
  );
}
