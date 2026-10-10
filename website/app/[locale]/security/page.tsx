import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section } from "@/components/ui/Section";
import { NakedList } from "@/components/story/Naked";
import { CommandCta } from "@/components/site/CommandCta";
import { Icon } from "@/components/ui/Icon";
import { MaskScrub, SecurityLayers } from "@/components/story/SecurityLayers";

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
    // The camera drops through the floor into a shaft, one plate per security layer, down to the locked core (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="vault" data-testid="vault">
      <div className="naked">
        <PageHero eyebrow={s.hero.eyebrow} title={s.hero.title} text={s.hero.text} align="center" />
      </div>

      <SecurityLayers dict={dict} locale={locale} />

      <Section>
        <div className="mx-auto max-w-3xl">
          <MaskScrub dict={dict} locale={locale} />
        </div>
      </Section>

      <div className="container-x naked grid gap-10 py-16 lg:grid-cols-[240px_1fr] md:py-24">
        <nav aria-label={dict.common.onThisPage} className="lg:sticky lg:top-[calc(var(--header-h)+1rem)] lg:self-start" data-reveal>
          <p className="label mb-2 text-[11px] font-semibold text-ink-3">{dict.common.onThisPage}</p>
          <ul className="flex flex-wrap gap-x-5 lg:flex-col lg:gap-0">
            {index.map((i) => (
              <li key={i.id}>
                <a href={`#${i.id}`} className="inline-flex min-h-11 items-center gap-2 text-sm font-medium text-ink-2 hover:text-accent-text">
                  <Icon name={i.icon} size={16} className="text-accent-text" />
                  {i.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex flex-col gap-20">
          <section id={sec.redaction.id} className="scroll-mt-24">
            <h2 className="display-2" data-split>{sec.redaction.title}</h2>
            <p className="mt-4 text-lg text-ink-2" data-reveal>{t(sec.redaction.text)}</p>
            <div className="mt-8 grid gap-10 md:grid-cols-2">
              <div className="border-t border-accent pt-5">
                <h3 className="flex items-center gap-2 font-semibold text-accent-text">
                  <Icon name="check" size={16} />
                  {sec.redaction.detected.title}
                </h3>
                <div className="mt-4"><NakedList items={sec.redaction.detected.items} render={t} /></div>
              </div>
              <div className="border-t border-border-2 pt-5">
                <h3 className="flex items-center gap-2 font-semibold">
                  <Icon name="alert" size={16} className="text-ink-3" />
                  {sec.redaction.limits.title}
                </h3>
                <div className="mt-4"><NakedList items={sec.redaction.limits.items} render={t} tone="muted" /></div>
              </div>
            </div>
            <div className="mt-10 border-t border-border-2 pt-5" data-reveal>
              <h3 className="font-semibold">{sec.redaction.controls.title}</h3>
              <div className="mt-4"><NakedList items={sec.redaction.controls.items} render={t} /></div>
            </div>
          </section>

          {[sec.isolation, sec.retention, sec.egress, sec.routing].map((block) => (
            <section key={block.id} id={block.id} className="scroll-mt-24">
              <h2 className="display-2" data-split>{block.title}</h2>
              <p className="mt-4 text-lg text-ink-2" data-reveal>{t(block.text)}</p>
              <div className="mt-6">
                <NakedList items={block.items} render={t} />
              </div>
            </section>
          ))}

          <section id={sec.access.id} className="scroll-mt-24">
            <h2 className="display-2" data-split>{sec.access.title}</h2>
            <div className="mt-6">
              <NakedList items={sec.access.items} render={t} />
            </div>
          </section>

          <section id={sec.notClaimed.id} className="scroll-mt-24 border-s-2 border-accent ps-6" data-reveal>
            <h2 className="flex items-center gap-3 text-2xl font-bold md:text-3xl">
              <Icon name="scale" size={26} className="text-accent-text" />
              {sec.notClaimed.title}
            </h2>
            <p className="mt-2 text-ink-2">{t(sec.notClaimed.text)}</p>
            <div className="mt-5"><NakedList items={sec.notClaimed.items} render={t} /></div>
          </section>
        </div>
      </div>
      <CommandCta locale={locale} dict={dict} naked />
    </div>
  );
}
