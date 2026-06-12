import type { Metadata } from "next";
import Link from "next/link";
import { ButtonLink } from "@/components/ui";

export const metadata: Metadata = {
  title: "Page Not Found",
  description:
    "The page you were looking for could not be found. Return to the Homayar homepage or browse the catalog.",
};

const SUGGESTED_LINKS = [
  { href: "/platform", label: "Platform" },
  { href: "/pricing", label: "Pricing" },
  { href: "/contact", label: "Contact" },
] as const;

export default function NotFound() {
  return (
    <section className="container-site py-24">
      <div className="mx-auto flex max-w-xl flex-col items-center text-center">
        <p className="font-mono text-7xl font-bold tracking-tight text-accent sm:text-8xl">
          404
        </p>
        <h1 className="mt-6 font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          This page drifted out of coverage.
        </h1>
        <p className="mt-4 text-base leading-relaxed text-ink-soft">
          The page you are looking for does not exist or has moved. Head back
          to the homepage or browse the catalog to find what you need.
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <ButtonLink href="/" variant="primary" size="lg">
            Back to home
          </ButtonLink>
          <ButtonLink href="/catalog" variant="secondary" size="lg">
            Browse the catalog
          </ButtonLink>
        </div>
        <nav aria-label="Suggested pages" className="mt-10">
          <p className="text-sm text-ink-muted">Or try one of these:</p>
          <ul className="mt-3 flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
            {SUGGESTED_LINKS.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  className="text-sm font-medium text-accent transition-colors hover:text-accent-strong"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </section>
  );
}
