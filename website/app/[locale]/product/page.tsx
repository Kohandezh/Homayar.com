import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section } from "@/components/ui/Section";
import { CheckList, FeatureCard } from "@/components/ui/Cards";
import { CtaBand } from "@/components/site/CtaBand";
import { CopilotChat, KpiGrid, RootCauses, ScoreBars, SentimentBars, Stages, TranscriptLines, TrendChart, UploadBox } from "@/components/mockups/parts";
import { Icon } from "@/components/ui/Icon";

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

  const anchors = [s.ingest, s.transcription, s.agents, s.dashboard, s.reports, s.copilot, s.admin];

  return (
    <>
      <PageHero eyebrow={p.hero.eyebrow} title={p.hero.title} text={p.hero.text}>
        <nav aria-label={dict.common.onThisPage} className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <ul className="flex w-max gap-2 pb-1">
            {anchors.map((a) => (
              <li key={a.id}>
                <a href={`#${a.id}`} className="inline-flex min-h-11 items-center rounded-full border border-border bg-surface px-4 text-sm font-semibold hover:border-primary hover:text-primary">
                  {a.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </PageHero>

      {/* Ingest */}
      <Section id={s.ingest.id}>
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <div>
            <p className="eyebrow">{s.ingest.label}</p>
            <h2 className="mt-3 text-2xl font-bold md:text-4xl">{s.ingest.title}</h2>
            <p className="mt-4 text-lg text-ink-2">{s.ingest.text}</p>
            <div className="mt-6">
              <CheckList items={s.ingest.bullets} />
            </div>
          </div>
          <div className="mock-window p-4" role="img" aria-label={mockLabel}>
            <div aria-hidden="true" className="flex flex-col gap-3">
              <UploadBox mock={m} />
              <div className="rounded-xl border border-border bg-bg p-3 text-[11px]">
                <p className="mb-2 font-semibold text-ink-2">VOIP webhook</p>
                <pre dir="ltr" className="overflow-x-auto rounded-lg bg-ink-teal p-3 text-[10px] leading-relaxed text-cream">{`POST /api/webhook/voip/call-event
X-Webhook-Secret: ••••••••
{ "extension": "104", "direction": "inbound",
  "duration": 372, "recording_base64": "…" }`}</pre>
              </div>
            </div>
          </div>
        </div>
      </Section>

      {/* Transcription */}
      <Section id={s.transcription.id} tone="sand">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <div className="mock-window order-2 p-4 lg:order-1" role="img" aria-label={mockLabel}>
            <div aria-hidden="true" className="flex flex-col gap-3">
              <Stages mock={m} />
              <div className="rounded-xl border border-border bg-bg p-3">
                <p className="mb-2 text-[10px] font-semibold text-ink-2">{m.transcript}</p>
                <TranscriptLines mock={m} />
              </div>
            </div>
          </div>
          <div className="order-1 lg:order-2">
            <p className="eyebrow">{s.transcription.label}</p>
            <h2 className="mt-3 text-2xl font-bold md:text-4xl">{s.transcription.title}</h2>
            <p className="mt-4 text-lg text-ink-2">{s.transcription.text}</p>
            <div className="mt-6">
              <CheckList items={s.transcription.bullets} />
            </div>
          </div>
        </div>
      </Section>

      {/* Agents */}
      <Section id={s.agents.id}>
        <p className="eyebrow">{s.agents.label}</p>
        <h2 className="mt-3 max-w-3xl text-2xl font-bold md:text-4xl">{s.agents.title}</h2>
        <p className="mt-4 max-w-3xl text-lg text-ink-2">{s.agents.text}</p>
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {s.agents.items.map((a, i) => (
            <FeatureCard key={a.title} icon={["gauge", "alert", "trend", "book"][i]} title={a.title} text={a.text} />
          ))}
        </div>
      </Section>

      {/* Dashboard */}
      <Section id={s.dashboard.id} tone="sand">
        <div className="grid items-center gap-10 lg:grid-cols-[1fr_1.2fr]">
          <div>
            <p className="eyebrow">{s.dashboard.label}</p>
            <h2 className="mt-3 text-2xl font-bold md:text-4xl">{s.dashboard.title}</h2>
            <p className="mt-4 text-lg text-ink-2">{s.dashboard.text}</p>
            <div className="mt-6">
              <CheckList items={s.dashboard.bullets} />
            </div>
          </div>
          <div className="mock-window p-4" role="img" aria-label={mockLabel}>
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

      {/* Reports */}
      <Section id={s.reports.id}>
        <div className="grid items-center gap-10 lg:grid-cols-[1.2fr_1fr]">
          <div className="mock-window order-2 p-4 lg:order-1" role="img" aria-label={mockLabel}>
            <div aria-hidden="true" className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-border bg-bg p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[11px] font-semibold">{m.qc}</span>
                  <span className="inline-flex items-center gap-1 rounded-full bg-surface px-2 py-0.5 text-[10px]">
                    <Icon name="download" size={10} />
                    {m.exportPdf}
                  </span>
                </div>
                <ScoreBars mock={m} locale={locale} />
              </div>
              <div className="flex flex-col gap-3">
                <div className="rounded-xl border border-border bg-bg p-3">
                  <p className="mb-2 text-[10px] font-semibold text-ink-2">{m.problems}</p>
                  <RootCauses mock={m} />
                </div>
                <div className="rounded-xl border border-border bg-bg p-3">
                  <p className="mb-2 text-[10px] font-semibold text-ink-2">{m.actions}</p>
                  <ul className="flex flex-col gap-1 text-[11px] text-ink-2">
                    {dict.home.outputs.items.slice(0, 2).map((o) => (
                      <li key={o.title} className="flex items-center gap-2">
                        <span className="size-1.5 rounded-full bg-accent" />
                        {o.title}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>
          <div className="order-1 lg:order-2">
            <p className="eyebrow">{s.reports.label}</p>
            <h2 className="mt-3 text-2xl font-bold md:text-4xl">{s.reports.title}</h2>
            <p className="mt-4 text-lg text-ink-2">{s.reports.text}</p>
            <div className="mt-6">
              <CheckList items={s.reports.bullets} />
            </div>
          </div>
        </div>
      </Section>

      {/* Copilot */}
      <Section id={s.copilot.id} tone="sand">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <div>
            <p className="eyebrow">{s.copilot.label}</p>
            <h2 className="mt-3 text-2xl font-bold md:text-4xl">{s.copilot.title}</h2>
            <p className="mt-4 text-lg text-ink-2">{s.copilot.text}</p>
            <div className="mt-6">
              <CheckList items={s.copilot.bullets} />
            </div>
          </div>
          <div className="mock-window p-4" role="img" aria-label={mockLabel}>
            <div aria-hidden="true">
              <CopilotChat mock={m} />
            </div>
          </div>
        </div>
      </Section>

      {/* Admin */}
      <Section id={s.admin.id}>
        <div className="rounded-[24px] border border-border bg-surface p-6 md:p-10">
          <p className="eyebrow">{s.admin.label}</p>
          <h2 className="mt-3 text-2xl font-bold md:text-3xl">{s.admin.title}</h2>
          <div className="mt-6 grid gap-6 md:grid-cols-2">
            <CheckList items={s.admin.bullets.slice(0, 2)} />
            <CheckList items={s.admin.bullets.slice(2)} />
          </div>
        </div>
      </Section>

      <CtaBand locale={locale} dict={dict} />
    </>
  );
}
