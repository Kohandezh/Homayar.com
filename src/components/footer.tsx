import Link from "next/link";
import { Logo } from "@/components/logo";
import { FOOTER_GROUPS, SITE } from "@/lib/site";

export function Footer() {
  return (
    <footer className="mt-auto border-t border-line bg-surface">
      <div className="container-site grid gap-10 py-14 md:grid-cols-[1.4fr_repeat(3,1fr)]">
        <div className="max-w-xs">
          <Logo />
          <p className="mt-4 text-sm leading-relaxed text-ink-muted">
            {SITE.tagline} Agentic AI voice analysis for call centers and
            enterprise customer-service teams.
          </p>
          <p className="mt-4 text-sm text-ink-faint">
            <span className="font-display">همایار</span> — Homa, the
            fortune-bird. Yar, the companion.
          </p>
        </div>

        {FOOTER_GROUPS.map((group) => (
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
          <p>
            © 2026 {SITE.legalName}. All rights reserved.{" "}
            <a
              href={`mailto:${SITE.contactEmail}`}
              className="hover:text-ink-muted"
            >
              {SITE.contactEmail}
            </a>
          </p>
          <p>Stripe checkout runs in test mode — no real charges.</p>
        </div>
      </div>
    </footer>
  );
}
