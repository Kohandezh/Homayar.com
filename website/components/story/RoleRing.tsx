import Link from "next/link";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { solutionSlugs } from "@/lib/site";
import { Icon } from "@/components/ui/Icon";
import { KpiGrid, RootCauses, ScoreBars, Stages } from "@/components/mockups/parts";

export function RoleRing({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const s = dict.solutions;
  const r = s.ring;
  const m = dict.mock;
  const views = {
    "qa-managers": <ScoreBars mock={m} locale={locale} limit={4} />,
    supervisors: <RootCauses mock={m} />,
    "contact-center-operations": <KpiGrid mock={m} compact />,
    "it-security": <Stages mock={m} />,
  };

  return (
    <section className="ring-section relative" data-ring data-testid="ring">
      <div className="ring-pin">
        <div className="container-x">
          <div className="ring-head">
            <p className="eyebrow">{r.eyebrow}</p>
            <h2 className="mt-3 text-2xl font-bold md:text-3xl">{r.sees}</h2>
            <p className="chip mt-4 ring-hint">
              <Icon name="scan" size={13} />
              {r.hint}
            </p>
          </div>
          <div className="ring-stage">
            <div className="ring-center panel panel-glow" data-ring-center>
              <div className="panel-head">
                <span className="window-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                <span className="text-sm font-semibold">{dict.demo.report}</span>
                <span className="chip ms-auto !min-h-0 !px-2 !py-0.5 !text-[10px]">{dict.demo.sample}</span>
              </div>
              <div className="ring-views">
                {solutionSlugs.map((slug) => (
                  <div key={slug} className="ring-view" data-ring-view={slug} data-testid="ring-view">
                    <p className="label mb-2 text-[10px] text-ink-3">
                      {r.sees}: {s.roles[slug].name}
                    </p>
                    <div aria-hidden="true">{views[slug]}</div>
                    <p className="mt-2 text-[11px] text-ink-2">{bidi(r.views[slug], locale)}</p>
                  </div>
                ))}
              </div>
            </div>
            <ul className="ring" data-ring-wheel aria-label={r.eyebrow}>
              {solutionSlugs.map((slug, i) => {
                const role = s.roles[slug];
                return (
                  <li key={slug} className={`ring-seat ${i === 0 ? "is-front" : ""}`} data-ring-seat style={{ ["--i" as string]: i }} data-testid="ring-seat">
                    <Link href={`/${locale}/solutions/${slug}`} className="ring-seat-card panel">
                      <div className="flex items-center justify-between">
                        <span className="label text-[11px] text-ink-3">
                          {r.seat} {localizeDigits(i + 1, locale)}
                        </span>
                        <Icon name={role.icon} size={20} className="text-accent-text" />
                      </div>
                      <h3 className="mt-3 text-xl font-bold md:text-2xl">{role.name}</h3>
                      <p className="mt-2 text-sm text-ink-2">{bidi(role.short, locale)}</p>
                      <span className="link-arrow mt-3 !min-h-0 text-sm">
                        {r.open}
                        <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={15} />
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}
