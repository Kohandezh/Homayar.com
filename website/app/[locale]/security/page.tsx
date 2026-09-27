import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero } from "@/components/ui/Section";
import { CheckList } from "@/components/ui/Cards";
import { CtaBand } from "@/components/site/CtaBand";
import { Icon } from "@/components/ui/Icon";

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
      <PageHero eyebrow={s.hero.eyebrow} title={s.hero.title} text={s.hero.text} />
      <div className="container-x grid gap-10 py-14 lg:grid-cols-[260px_1fr] md:py-20">
        {/* Sticky index on desktop */}
        <nav aria-label={dict.common.onThisPage} className="lg:sticky lg:top-[calc(var(--header-h)+1rem)] lg:self-start">
          <p className="mb-2 text-xs font-semibold text-ink-2">{dict.common.onThisPage}</p>
          <ul className="flex flex-wrap gap-2 lg:flex-col lg:gap-1">
            {index.map((i) => (
              <li key={i.id}>
                <a href={`#${i.id}`} className="inline-flex min-h-11 items-center gap-2 rounded-full border border-border bg-surface px-3 text-sm font-medium hover:border-primary hover:text-primary lg:w-full lg:border-transparent lg:bg-transparent lg:hover:bg-sand">
                  <Icon name={i.icon} size={16} />
                  {i.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex flex-col gap-12">
          {/* Redaction */}
          <section id={sec.redaction.id} className="scroll-mt-24">
            <h2 className="text-2xl font-bold md:text-3xl">{sec.redaction.title}</h2>
            <p className="mt-3 text-lg text-ink-2">{sec.redaction.text}</p>
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <div className="card p-5">
                <h3 className="flex items-center gap-2 font-semibold">
                  <span className="text-success"><Icon name="check" size={18} /></span>
                  {sec.redaction.detected.title}
                </h3>
                <ul className="mt-3 flex flex-col gap-2 text-ink-2">
                  {sec.redaction.detected.items.map((i) => <li key={i} className="flex gap-2"><span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-primary" />{i}</li>)}
                </ul>
              </div>
              <div className="rounded-[12px] border border-accent/50 bg-[#fff6e5] p-5">
                <h3 className="flex items-center gap-2 font-semibold text-accent-text">
                  <Icon name="alert" size={18} />
                  {sec.redaction.limits.title}
                </h3>
                <ul className="mt-3 flex flex-col gap-2 text-ink-2">
                  {sec.redaction.limits.items.map((i) => <li key={i} className="flex gap-2"><span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-accent" />{i}</li>)}
                </ul>
              </div>
            </div>
            <div className="mt-4 rounded-[12px] border border-border bg-surface p-5">
              <h3 className="font-semibold">{sec.redaction.controls.title}</h3>
              <div className="mt-3"><CheckList items={sec.redaction.controls.items} /></div>
            </div>
          </section>

          {[sec.isolation, sec.retention, sec.egress, sec.routing].map((block, idx) => (
            <section key={block.id} id={block.id} className="scroll-mt-24">
              <h2 className="text-2xl font-bold md:text-3xl">{block.title}</h2>
              <p className="mt-3 text-lg text-ink-2">{block.text}</p>
              <div className={`mt-6 rounded-[16px] p-5 md:p-6 ${idx % 2 === 0 ? "bg-sand" : "border border-border bg-surface"}`}>
                <CheckList items={block.items} />
              </div>
            </section>
          ))}

          <section id={sec.access.id} className="scroll-mt-24">
            <h2 className="text-2xl font-bold md:text-3xl">{sec.access.title}</h2>
            <div className="mt-6 rounded-[16px] bg-sand p-5 md:p-6">
              <CheckList items={sec.access.items} />
            </div>
          </section>

          <section id={sec.notClaimed.id} className="scroll-mt-24 rounded-[20px] bg-ink-teal p-6 text-cream md:p-8">
            <h2 className="flex items-center gap-2 text-2xl font-bold md:text-3xl">
              <Icon name="scale" size={26} />
              {sec.notClaimed.title}
            </h2>
            <p className="mt-2 text-cream/80">{sec.notClaimed.text}</p>
            <div className="mt-5"><CheckList items={sec.notClaimed.items} tone="dark" /></div>
          </section>
        </div>
      </div>
      <CtaBand locale={locale} dict={dict} />
    </>
  );
}
