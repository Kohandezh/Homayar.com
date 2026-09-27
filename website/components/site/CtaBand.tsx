import Link from "next/link";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";

export function CtaBand({ locale, dict }: { locale: Locale; dict: Dictionary }) {
  return (
    <section className="py-14 md:py-20">
      <div className="container-x">
        <div className="relative overflow-hidden rounded-[24px] bg-ink-teal px-6 py-10 text-cream md:px-12 md:py-14">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -end-16 -top-16 size-64 rounded-full opacity-40"
            style={{ background: "radial-gradient(closest-side, rgba(217,119,6,0.55), transparent)" }}
          />
          <div className="relative grid gap-6 md:grid-cols-[1.5fr_1fr] md:items-center">
            <div>
              <h2 className="text-2xl font-bold md:text-3xl">{dict.ctaBand.title}</h2>
              <p className="mt-3 max-w-xl text-cream/85">{dict.ctaBand.text}</p>
            </div>
            <div className="flex flex-wrap gap-3 md:justify-end">
              <Link href={`/${locale}/contact`} className="btn btn-on-dark">
                {dict.ctaBand.primary}
              </Link>
              <Link href={`/${locale}/how-it-works`} className="btn btn-ghost-on-dark">
                {dict.ctaBand.secondary}
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
