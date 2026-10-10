import Link from "next/link";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { solutionSlugs } from "@/lib/site";
import { Icon } from "@/components/ui/Icon";
import { KpiGrid, RootCauses, ScoreBars, Stages } from "@/components/mockups/parts";

// Solutions: the roles stand in a ring around a glowing core (design-system/SCENE-RULES.md).
// With WebGL the engine hangs each role on the ring and turns it while the page scrolls.
// Without WebGL the roles simply stack.
export function RoleOrbit({ dict, locale }: { dict: Dictionary; locale: Locale }) {
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
    <section className="roles relative" data-roles data-testid="roles" style={{ ["--n" as string]: solutionSlugs.length }}>
      <div className="roles-head naked text-center">
        <p className="eyebrow justify-center">{r.eyebrow}</p>
        <h2 className="mt-2 text-2xl font-bold md:text-3xl">{r.sees}</h2>
      </div>
      <div className="html3d-layer" data-html-layer>
        <div className="html3d-camera roles-list" data-html-camera>
          {solutionSlugs.map((slug, i) => {
            const role = s.roles[slug];
            return (
              <article key={slug} className="role-panel" data-role data-testid="role">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-ink-3">
                    {r.seat} {localizeDigits(i + 1, locale)}
                  </span>
                  <Icon name={role.icon} size={22} className="text-accent-text" />
                </div>
                <h3 className="mt-3 text-2xl font-bold md:text-3xl">{role.name}</h3>
                <p className="mt-2 text-ink-2">{bidi(role.short, locale)}</p>
                <div className="mt-5 border-t border-border-2 pt-4">
                  <p className="mb-3 text-xs font-semibold text-accent-text">{bidi(r.views[slug], locale)}</p>
                  <div aria-hidden="true">{views[slug]}</div>
                </div>
                <Link href={`/${locale}/solutions/${slug}`} className="link-arrow mt-4">
                  {r.open}
                  <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={15} />
                </Link>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
