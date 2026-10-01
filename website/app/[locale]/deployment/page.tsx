import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section } from "@/components/ui/Section";
import { CheckList, Chip } from "@/components/ui/Cards";
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
    <>
      <PageHero eyebrow={d.hero.eyebrow} title={d.hero.title} text={d.hero.text} />

      <DeployZones dict={dict} locale={locale} />

      <Section tone="raised">
        <div className="grid gap-4 lg:grid-cols-3">
          {d.options.map((o, i) => (
            <article key={o.id} id={o.id} className={`panel scroll-mt-24 flex flex-col gap-5 p-6 ${i === 1 ? "panel-glow" : ""}`} data-reveal={String(i * 0.08)}>
              <div className="flex items-center gap-3">
                <Chip tone={i === 1 ? "accent" : "default"}>{modes[i]}</Chip>
                <span className="label text-xs text-ink-3">{o.id}</span>
              </div>
              <div>
                <h2 className="text-2xl font-bold md:text-3xl">{o.name}</h2>
                <p className="mt-2 text-ink-2">{t(o.summary)}</p>
              </div>
              <div className="rounded-xl border border-border-2 bg-bg-2 p-3">
                <FlowDiagram flow={d.flow} mode={modes[i]} compact />
              </div>
              <CheckList items={o.points} render={t} />
              <p className="mt-auto rounded-xl border border-border-2 bg-surface-2 p-3 text-sm text-ink-2">{t(o.fit)}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h2 className="display-2" data-split>{d.compare.title}</h2>
          <p className="chip compare-hint">
            <Icon name="scan" size={13} />
            {d.zones.compareHint}
          </p>
        </div>
        <div className="panel mt-8 overflow-x-auto" data-table-build data-testid="compare-table">
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
      </Section>

      <Section tone="raised">
        <div className="panel panel-glow grid gap-6 p-6 md:grid-cols-[1fr_1fr] md:items-center md:p-10" data-reveal>
          <h2 className="display-2">{d.runtimeNote.title}</h2>
          <ul className="grid gap-3 sm:grid-cols-2" data-reveal-group>
            {d.runtimeNote.items.map((i) => (
              <li key={i} className="flex items-center gap-3 rounded-xl border border-border-2 bg-bg-2 px-4 py-3 font-medium">
                <span className="inline-grid size-7 shrink-0 place-items-center rounded-full border border-teal-deep/50 text-accent-text">
                  <Icon name="x" size={14} strokeWidth={2.5} />
                </span>
                <span>{t(i)}</span>
              </li>
            ))}
          </ul>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
