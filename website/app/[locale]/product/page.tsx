import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { Section } from "@/components/ui/Section";
import { CheckList, Chip } from "@/components/ui/Cards";
import { CommandCta } from "@/components/site/CommandCta";
import { SectionTabs } from "@/components/site/SectionTabs";
import { LiveDemo } from "@/components/demo/LiveDemo";
import { CopilotChat, KpiGrid, RootCauses, ScoreBars, SentimentBars, Stages, TranscriptLines, TrendChart, UploadBox } from "@/components/mockups/parts";
import { Icon } from "@/components/ui/Icon";
import { SceneBackdrop } from "@/components/scene/SceneBackdrop";

export async function generateMetadata({ params }: PageProps<"/[locale]/product">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/product", title: dict.product.meta.title, description: dict.product.meta.description, siteName: dict.brand });
}

export default async function ProductPage({ params }: PageProps<"/[locale]/product">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const p = dict.product;
  const s = p.sections;
  const m = dict.mock;
  const mockLabel = dict.common.decorativeMockup;
  const t = (text: string) => bidi(text, locale);

  const anchors = [s.ingest, s.transcription, s.agents, s.dashboard, s.reports, s.copilot, s.admin];

  return (
    <>
      <section className="relative isolate overflow-hidden border-b border-border pb-10 pt-14 md:pt-20" data-scene="tunnel" data-hero-scene>
        <SceneBackdrop kind="tunnel" priority />
        <div className="scene-fade-bottom" aria-hidden="true" />
        <div className="container-x">
          <div className="mx-auto max-w-3xl text-center" data-hero-content>
            <p className="eyebrow justify-center" data-reveal>
              {p.hero.eyebrow}
            </p>
            <h1 className="display mt-5" data-split>
              {p.hero.title}
            </h1>
            <p className="mt-6 text-lg text-ink-2 md:text-xl" data-reveal>
              {p.hero.text}
            </p>
          </div>
          <div className="mt-12" data-depth="420">
            <LiveDemo demo={dict.demo} mock={m} locale={locale} brand={dict.brand} frame="window" />
          </div>
        </div>
      </section>

      <SectionTabs items={anchors.map((a) => ({ id: a.id, label: a.label }))} label={dict.common.onThisPage} />

      <Section id={s.ingest.id} className="scroll-mt-28" scene="field">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <div>
            <p className="eyebrow" data-reveal>{s.ingest.label}</p>
            <h2 className="display-2 mt-4" data-split>{s.ingest.title}</h2>
            <p className="mt-5 text-lg text-ink-2" data-reveal>{t(s.ingest.text)}</p>
            <div className="mt-6"><CheckList items={s.ingest.bullets} render={t} /></div>
          </div>
          <div className="panel p-4" role="img" aria-label={mockLabel} data-depth="320">
            <div aria-hidden="true" className="flex flex-col gap-3">
              <UploadBox mock={m} />
              <div className="rounded-xl border border-border-2 bg-bg-2 p-3 text-[11px]">
                <p className="label mb-2 font-semibold text-ink-3">VOIP webhook</p>
                <pre dir="ltr" className="label overflow-x-auto rounded-lg bg-bg p-3 text-[10px] leading-relaxed text-accent-text">{`POST /api/webhook/voip/call-event
X-Webhook-Secret: ••••••••
{ "extension": "104", "direction": "inbound",
  "duration": 372, "recording_base64": "…" }`}</pre>
              </div>
            </div>
          </div>
        </div>
      </Section>

      <Section id={s.transcription.id} tone="raised" className="scroll-mt-28">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <div className="panel order-2 p-4 lg:order-1" role="img" aria-label={mockLabel} data-depth="320">
            <div aria-hidden="true" className="flex flex-col gap-3">
              <Stages mock={m} />
              <div className="rounded-xl border border-border-2 bg-bg-2 p-3">
                <p className="mb-2 text-[10px] font-semibold text-ink-3">{m.transcript}</p>
                <TranscriptLines mock={m} />
              </div>
            </div>
          </div>
          <div className="order-1 lg:order-2">
            <p className="eyebrow" data-reveal>{s.transcription.label}</p>
            <h2 className="display-2 mt-4" data-split>{s.transcription.title}</h2>
            <p className="mt-5 text-lg text-ink-2" data-reveal>{t(s.transcription.text)}</p>
            <div className="mt-6"><CheckList items={s.transcription.bullets} render={t} /></div>
          </div>
        </div>
      </Section>

      <Section id={s.agents.id} tone="glow" className="scroll-mt-28" scene="field">
        <p className="eyebrow" data-reveal>{s.agents.label}</p>
        <h2 className="display-2 mt-4 max-w-3xl" data-split>{s.agents.title}</h2>
        <p className="mt-5 max-w-3xl text-lg text-ink-2" data-reveal>{t(s.agents.text)}</p>
        <ol className="mt-12 grid gap-px overflow-hidden rounded-2xl border border-border-2 bg-border-2 sm:grid-cols-2 lg:grid-cols-4" data-reveal-group>
          {s.agents.items.map((a, i) => (
            <li key={a.title} className="flex flex-col gap-3 bg-surface p-6">
              <span className="label text-xs text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
              <h3 className="flex items-center gap-2 text-lg font-semibold">
                <Icon name={["gauge", "alert", "trend", "book"][i]} size={18} className="text-accent-text" />
                {t(a.title)}
              </h3>
              <p className="text-sm text-ink-2">{t(a.text)}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section id={s.dashboard.id} className="scroll-mt-28" scene="field">
        <div className="grid items-center gap-10 lg:grid-cols-[1fr_1.2fr]">
          <div>
            <p className="eyebrow" data-reveal>{s.dashboard.label}</p>
            <h2 className="display-2 mt-4" data-split>{s.dashboard.title}</h2>
            <p className="mt-5 text-lg text-ink-2" data-reveal>{t(s.dashboard.text)}</p>
            <div className="mt-6"><CheckList items={s.dashboard.bullets} render={t} /></div>
          </div>
          <div className="panel panel-glow p-4" role="img" aria-label={mockLabel} data-depth="360">
            <div aria-hidden="true" className="flex flex-col gap-3">
              <KpiGrid mock={m} />
              <div className="grid gap-3 sm:grid-cols-2">
                <TrendChart mock={m} />
                <SentimentBars mock={m} />
              </div>
            </div>
          </div>
        </div>
      </Section>

      <Section id={s.reports.id} tone="raised" className="scroll-mt-28">
        <div className="grid items-center gap-10 lg:grid-cols-[1.2fr_1fr]">
          <div className="panel order-2 p-4 lg:order-1" role="img" aria-label={mockLabel} data-depth="320">
            <div aria-hidden="true" className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-border-2 bg-bg-2 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[11px] font-semibold">{m.qc}</span>
                  <span className="chip !min-h-0 !px-2 !py-0.5 !text-[10px]">
                    <Icon name="download" size={10} />
                    {m.exportPdf}
                  </span>
                </div>
                <ScoreBars mock={m} locale={locale} />
              </div>
              <div className="flex flex-col gap-3">
                <div className="rounded-xl border border-border-2 bg-bg-2 p-3">
                  <p className="mb-2 text-[10px] font-semibold text-ink-3">{m.problems}</p>
                  <RootCauses mock={m} />
                </div>
                <div className="rounded-xl border border-border-2 bg-bg-2 p-3">
                  <p className="mb-2 text-[10px] font-semibold text-ink-3">{m.actions}</p>
                  <ul className="flex flex-col gap-1 text-[11px] text-ink-2">
                    {dict.demo.agents.teacher.items.slice(0, 2).map((o) => (
                      <li key={o} className="flex items-start gap-2">
                        <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-accent" />
                        {o}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>
          <div className="order-1 lg:order-2">
            <p className="eyebrow" data-reveal>{s.reports.label}</p>
            <h2 className="display-2 mt-4" data-split>{s.reports.title}</h2>
            <p className="mt-5 text-lg text-ink-2" data-reveal>{t(s.reports.text)}</p>
            <div className="mt-6"><CheckList items={s.reports.bullets} render={t} /></div>
          </div>
        </div>
      </Section>

      <Section id={s.copilot.id} className="scroll-mt-28" scene="field">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <div>
            <p className="eyebrow" data-reveal>{s.copilot.label}</p>
            <h2 className="display-2 mt-4" data-split>{s.copilot.title}</h2>
            <p className="mt-5 text-lg text-ink-2" data-reveal>{t(s.copilot.text)}</p>
            <div className="mt-6"><CheckList items={s.copilot.bullets} render={t} /></div>
          </div>
          <div className="panel panel-glow p-4" role="img" aria-label={mockLabel} data-depth="360">
            <div aria-hidden="true">
              <div className="mb-3 flex items-center gap-2 rounded-lg border border-border-2 bg-bg-2 px-3 py-2 text-[11px] text-ink-3">
                <Icon name="sparkles" size={12} className="text-accent-text" />
                {m.nav[4]}
                <span className="kbd ms-auto">⌘K</span>
              </div>
              <CopilotChat mock={m} />
            </div>
          </div>
        </div>
      </Section>

      <Section id={s.admin.id} tone="raised" className="scroll-mt-28">
        <div className="panel p-6 md:p-10" data-depth="260">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="eyebrow">{s.admin.label}</p>
              <h2 className="display-2 mt-4">{s.admin.title}</h2>
            </div>
            <Chip tone="accent" live>
              LDAP · AD
            </Chip>
          </div>
          <div className="mt-8 grid gap-6 md:grid-cols-2">
            <CheckList items={s.admin.bullets.slice(0, 2)} render={t} />
            <CheckList items={s.admin.bullets.slice(2)} render={t} />
          </div>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
