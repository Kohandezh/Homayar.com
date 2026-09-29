import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero } from "@/components/ui/Section";
import { CheckList, Chip } from "@/components/ui/Cards";
import { CommandCta } from "@/components/site/CommandCta";
import { Icon } from "@/components/ui/Icon";
import { SceneBackdrop } from "@/components/scene/SceneBackdrop";

export async function generateMetadata({ params }: PageProps<"/[locale]/security">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/security", title: dict.security.meta.title, description: dict.security.meta.description, siteName: dict.brand });
}

export default async function SecurityPage({ params }: PageProps<"/[locale]/security">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const s = dict.security;
  const sec = s.sections;
  const md = s.maskDemo;
  const t = (text: string) => bidi(text, locale);
  const index = [
    { id: sec.redaction.id, title: sec.redaction.title, icon: "eyeOff" },
    { id: sec.isolation.id, title: sec.isolation.title, icon: "layers" },
    { id: sec.retention.id, title: sec.retention.title, icon: "trash" },
    { id: sec.egress.id, title: sec.egress.title, icon: "server" },
    { id: sec.routing.id, title: sec.routing.title, icon: "shield" },
    { id: sec.access.id, title: sec.access.title, icon: "lock" },
    { id: sec.notClaimed.id, title: sec.notClaimed.title, icon: "scale" },
  ];

  return (
    <>
      <PageHero
        scene="warp"
        eyebrow={s.hero.eyebrow}
        title={s.hero.title}
        text={s.hero.text}
        aside={
          <div className="panel panel-glow p-4" data-mask>
            <div className="flex items-center justify-between gap-2">
              <p className="eyebrow !text-[11px]">{md.eyebrow}</p>
              <span className="mask-badge chip chip-accent !text-[11px]">
                <Icon name="shieldCheck" size={12} />
                {md.badge}
              </span>
            </div>
            <h2 className="mt-3 text-lg font-semibold">{md.title}</h2>
            <div className="relative mt-4 min-h-[88px]">
              <div className="mask-original absolute inset-0 gap-3 rounded-xl border border-teal-deep/40 bg-bg-2 p-3 text-sm">
                <span className="label w-16 shrink-0 text-[10px] font-semibold text-accent-text">{md.who}</span>
                <span className="text-ink-2">{md.before}</span>
              </div>
              <div className="mask-masked flex gap-3 rounded-xl border border-accent/40 bg-bg-2 p-3 text-sm">
                <span className="label w-16 shrink-0 text-[10px] font-semibold text-accent-text">{md.who}</span>
                <span className="text-ink-2">{t(md.after)}</span>
              </div>
            </div>
            <div className="label mt-3 flex justify-between text-[10px] text-ink-3">
              <span>{md.original}</span>
              <span>{md.masked}</span>
            </div>
          </div>
        }
      />

      <div className="relative isolate overflow-hidden" data-scene="field">
      <SceneBackdrop kind="field" />
      <div className="container-x grid gap-10 py-16 lg:grid-cols-[260px_1fr] md:py-24">
        <nav aria-label={dict.common.onThisPage} className="lg:sticky lg:top-[calc(var(--header-h)+1rem)] lg:self-start" data-reveal>
          <p className="label mb-2 text-[11px] font-semibold text-ink-3">{dict.common.onThisPage}</p>
          <ul className="flex flex-wrap gap-2 lg:flex-col lg:gap-0.5">
            {index.map((i) => (
              <li key={i.id}>
                <a href={`#${i.id}`} className="inline-flex min-h-11 items-center gap-2 rounded-full border border-border-2 bg-surface px-3 text-sm font-medium hover:border-accent/50 hover:text-accent-text lg:w-full lg:border-transparent lg:bg-transparent lg:hover:bg-surface-2">
                  <Icon name={i.icon} size={16} className="text-accent-text" />
                  {i.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex flex-col gap-14">
          <section id={sec.redaction.id} className="scroll-mt-24">
            <h2 className="display-2" data-split>{sec.redaction.title}</h2>
            <p className="mt-4 text-lg text-ink-2" data-reveal>{t(sec.redaction.text)}</p>
            <div className="mt-6 grid gap-4 md:grid-cols-2" data-reveal-group>
              <div className="panel p-5">
                <h3 className="flex items-center gap-2 font-semibold">
                  <Chip tone="accent">
                    <Icon name="check" size={12} />
                    {sec.redaction.detected.title}
                  </Chip>
                </h3>
                <ul className="mt-4 flex flex-col gap-2 text-ink-2">
                  {sec.redaction.detected.items.map((i) => (
                    <li key={i} className="flex gap-2">
                      <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-accent" />
                      {t(i)}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="panel border-teal-deep/40 p-5">
                <h3 className="flex items-center gap-2 font-semibold">
                  <Chip tone="strong">
                    <Icon name="alert" size={12} />
                    {sec.redaction.limits.title}
                  </Chip>
                </h3>
                <ul className="mt-4 flex flex-col gap-2 text-ink-2">
                  {sec.redaction.limits.items.map((i) => (
                    <li key={i} className="flex gap-2">
                      <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-teal-deep" />
                      {t(i)}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="panel mt-4 p-5" data-reveal>
              <h3 className="font-semibold">{sec.redaction.controls.title}</h3>
              <div className="mt-3"><CheckList items={sec.redaction.controls.items} render={t} /></div>
            </div>
          </section>

          {[sec.isolation, sec.retention, sec.egress, sec.routing].map((block, idx) => (
            <section key={block.id} id={block.id} className="scroll-mt-24">
              <h2 className="display-2" data-split>{block.title}</h2>
              <p className="mt-4 text-lg text-ink-2" data-reveal>{t(block.text)}</p>
              <div className={`mt-6 rounded-2xl p-5 md:p-6 ${idx % 2 === 0 ? "bg-bg-deep border border-border" : "panel"}`} data-depth="240">
                <CheckList items={block.items} render={t} />
              </div>
            </section>
          ))}

          <section id={sec.access.id} className="scroll-mt-24">
            <h2 className="display-2" data-split>{sec.access.title}</h2>
            <div className="mt-6 rounded-2xl border border-border bg-bg-2 p-5 md:p-6" data-reveal>
              <CheckList items={sec.access.items} render={t} />
            </div>
          </section>

          <section id={sec.notClaimed.id} className="panel panel-glow scroll-mt-24 p-6 md:p-8" data-depth="260">
            <h2 className="flex items-center gap-3 text-2xl font-bold md:text-3xl">
              <Icon name="scale" size={26} className="text-accent-text" />
              {sec.notClaimed.title}
            </h2>
            <p className="mt-2 text-ink-2">{t(sec.notClaimed.text)}</p>
            <div className="mt-5"><CheckList items={sec.notClaimed.items} render={t} /></div>
          </section>
        </div>
      </div>
      </div>
      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
