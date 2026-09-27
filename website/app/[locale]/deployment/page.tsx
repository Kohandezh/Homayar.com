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

      <Section>
        <div className="flex flex-col gap-6">
          {d.options.map((o, i) => (
            <article key={o.id} id={o.id} className={`panel grid gap-6 p-6 md:p-8 lg:grid-cols-[1fr_1.2fr] ${i === 1 ? "panel-glow" : ""}`} data-reveal>
              <div>
                <div className="flex items-center gap-3">
                  <Chip tone={i === 1 ? "amber" : "default"}>{modes[i]}</Chip>
                  <span className="mono text-xs text-ink-3">{o.id}</span>
                </div>
                <h2 className="display-2 mt-4">{o.name}</h2>
                <p className="mt-2 text-lg text-ink-2">{t(o.summary)}</p>
                <div className="mt-6 rounded-xl border border-border-2 bg-bg-2 p-4">
                  <FlowDiagram flow={d.flow} mode={modes[i]} />
                </div>
                <p className="mt-4 rounded-xl border border-border-2 bg-surface-2 p-3 text-sm text-ink-2">{t(o.fit)}</p>
              </div>
              <div className="lg:border-s lg:border-border-2 lg:ps-8">
                <CheckList items={o.points} render={t} />
              </div>
            </article>
          ))}
        </div>
      </Section>

      <Section tone="raised">
        <h2 className="display-2" data-split>{d.compare.title}</h2>
        <div className="panel mt-8 overflow-x-auto" data-reveal>
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

      <Section>
        <div className="panel panel-glow grid gap-6 p-6 md:grid-cols-[1fr_1fr] md:items-center md:p-10" data-reveal>
          <h2 className="display-2">{d.runtimeNote.title}</h2>
          <ul className="grid gap-3 sm:grid-cols-2" data-reveal-group>
            {d.runtimeNote.items.map((i) => (
              <li key={i} className="flex items-center gap-3 rounded-xl border border-border-2 bg-bg-2 px-4 py-3 font-medium">
                <span className="inline-grid size-7 shrink-0 place-items-center rounded-full border border-ember/50 text-ember">
                  <Icon name="x" size={14} strokeWidth={2.5} />
                </span>
                {t(i)}
              </li>
            ))}
          </ul>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
