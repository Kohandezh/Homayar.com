import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section } from "@/components/ui/Section";
import { CommandCta } from "@/components/site/CommandCta";
import { FlowDiagram } from "@/components/story/FlowDiagram";
import { DeployZones } from "@/components/story/DeployZones";
import { Icon } from "@/components/ui/Icon";

export async function generateMetadata({ params }: PageProps<"/[locale]/deployment">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/deployment", title: dict.deployment.meta.title, description: dict.deployment.meta.description, siteName: dict.brand });
}

const modes = ["saas", "self-hosted", "hybrid"] as const;

export default async function DeploymentPage({ params }: PageProps<"/[locale]/deployment">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const d = dict.deployment;
  const t = (text: string) => bidi(text, locale);

  return (
    // A map seen from high above. The camera comes down to the zone the Homayar block is in (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="map" data-testid="map">
      <div className="naked">
        <PageHero eyebrow={d.hero.eyebrow} title={d.hero.title} text={d.hero.text} align="center" />
      </div>

      <DeployZones dict={dict} locale={locale} />

      <Section>
        <div className="naked grid gap-14 lg:grid-cols-3 lg:gap-10">
          {d.options.map((o, i) => (
            <article key={o.id} id={o.id} className="scroll-mt-24 flex flex-col gap-5 border-t border-border-2 pt-6" data-reveal={String(i * 0.08)}>
              <div className="flex items-end gap-4">
                <span className="outline-num" aria-hidden="true">
                  {localizeDigits(String(i + 1).padStart(2, "0"), locale)}
                </span>
                <span className={`label pb-2 text-xs font-semibold ${i === 1 ? "text-accent-text" : "text-ink-3"}`}>{modes[i]}</span>
              </div>
              <div>
                <h2 className={`text-2xl font-bold md:text-3xl ${i === 1 ? "text-accent-text" : ""}`}>{o.name}</h2>
                <p className="mt-2 text-ink-2">{t(o.summary)}</p>
              </div>
              <div className="max-w-xs">
                <FlowDiagram flow={d.flow} mode={modes[i]} compact naked />
              </div>
              <ul className="flex flex-col gap-3">
                {o.points.map((pt) => (
                  <li key={pt} className="flex items-start gap-3 text-ink-2">
                    <span className="glow-dot mt-2" aria-hidden="true" />
                    <span>{t(pt)}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-auto border-s-2 border-accent ps-4 text-sm text-ink-2">{t(o.fit)}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section>
        <div className="naked">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <h2 className="display-2" data-split>{d.compare.title}</h2>
            <p className="compare-hint label flex items-center gap-2 text-xs text-ink-3">
              <Icon name="scan" size={13} />
              {d.zones.compareHint}
            </p>
          </div>
          <div className="mt-8 overflow-x-auto" data-table-build data-testid="compare-table">
            <table className="table-clean min-w-[640px]">
              <thead>
                <tr>
                  {d.compare.columns.map((c, i) => (
                    <th key={i} scope="col">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.compare.rows.map((row) => (
                  <tr key={row[0]}>
                    <th scope="row" className="font-semibold">{row[0]}</th>
                    {row.slice(1).map((cell, i) => (
                      <td key={i} className="text-ink-2">{t(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Section>

      <Section>
        <div className="naked grid gap-10 md:grid-cols-[1fr_1fr] md:items-center" data-reveal>
          <h2 className="display-2">{d.runtimeNote.title}</h2>
          <ul className="grid gap-x-8 gap-y-5 sm:grid-cols-2" data-reveal-group>
            {d.runtimeNote.items.map((i) => (
              <li key={i} className="flex items-center gap-3 font-medium">
                <Icon name="x" size={16} strokeWidth={2.5} className="shrink-0 text-accent-text" />
                <span>{t(i)}</span>
              </li>
            ))}
          </ul>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} naked />
    </div>
  );
}
