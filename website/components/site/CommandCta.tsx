import Link from "next/link";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";

const icons = ["mail", "activity", "shieldCheck", "server"];

export function CommandCta({ locale, dict }: { locale: Locale; dict: Dictionary }) {
  const p = dict.palette;
  return (
    <section className="relative isolate overflow-hidden py-20 md:py-28">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        <div className="glow-amber absolute start-1/2 top-1/2 h-[520px] w-[820px] -translate-x-1/2 -translate-y-1/2 rtl:translate-x-1/2" data-parallax="6" />
        <div className="absolute inset-0 grid-lines" />
      </div>
      <div className="container-x">
        <div className="mx-auto flex max-w-2xl flex-col items-center text-center">
          <p className="eyebrow" data-reveal>
            {p.eyebrow}
          </p>
          <h2 className="display-2 mt-4" data-split>
            {p.title}
          </h2>
        </div>
        <div className="panel panel-glow mx-auto mt-10 max-w-2xl overflow-hidden" data-reveal>
          <div className="flex items-center gap-3 border-b border-border-2 px-4 py-3.5 text-ink-3">
            <Icon name="sparkles" size={18} className="text-amber-text" />
            <span className="flex-1 text-start text-base text-ink-2">{p.placeholder}</span>
            <span className="kbd" aria-hidden="true">
              ⌘K
            </span>
          </div>
          <p className="mono px-4 pt-3 text-[11px] font-semibold text-ink-3">{p.hint}</p>
          <ul className="flex flex-col gap-1 p-2">
            {p.actions.map((a, i) => (
              <li key={a.path}>
                <Link href={`/${locale}${a.path}`} className="palette-row group" data-magnetic={i === 0 ? "" : undefined}>
                  <span className={`inline-grid size-9 shrink-0 place-items-center rounded-lg border ${i === 0 ? "border-amber/40 bg-amber/10 text-amber-text" : "border-border-2 bg-bg-2 text-ink-2"}`}>
                    <Icon name={icons[i]} size={17} />
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col text-start">
                    <span className="font-semibold">{a.label}</span>
                    <span className="truncate text-sm text-ink-2">{a.meta}</span>
                  </span>
                  <span className="kbd hidden sm:inline-flex" aria-hidden="true">
                    {a.kbd}
                  </span>
                  <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={16} className="text-ink-3 transition-transform group-hover:translate-x-1 rtl:group-hover:-translate-x-1" />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
