import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { ContactForm } from "@/components/site/ContactForm";
import { Icon } from "@/components/ui/Icon";
import { VoiceMarkLive } from "@/components/site/VoiceMarkLive";

export async function generateMetadata({ params }: PageProps<"/[locale]/contact">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/contact", title: dict.contact.meta.title, description: dict.contact.meta.description, siteName: dict.brand });
}

export default async function ContactPage({ params }: PageProps<"/[locale]/contact">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const c = dict.contact;
  const links = [
    { label: dict.nav.howItWorks, href: `/${locale}/how-it-works` },
    { label: dict.nav.security, href: `/${locale}/security` },
    { label: dict.nav.deployment, href: `/${locale}/deployment` },
  ];

  return (
    // The camera drifts slowly toward one bright beacon on the grid. The form floats beside it (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="beacon" data-voice-scope data-testid="contact-calm">
      <div className="container-x naked grid gap-10 py-14 md:py-20 lg:grid-cols-[1.2fr_1fr] lg:gap-16">
        <div>
          <p className="eyebrow" data-reveal>{c.hero.eyebrow}</p>
          <h1 className="display mt-5" data-split>{c.hero.title}</h1>
          <p className="mt-5 max-w-xl text-lg text-ink-2" data-reveal>{c.hero.text}</p>
          <div className="mt-10 border-t border-accent pt-8" data-reveal="0.1">
            <ContactForm copy={c.form} locale={locale} />
          </div>
        </div>
        <aside className="lg:sticky lg:top-[calc(var(--header-h)+1.5rem)] lg:self-start" data-reveal="0.15">
          <VoiceMarkLive label={dict.brand} />
          <h2 className="mt-4 text-xl font-bold">{c.aside.title}</h2>
          <ol className="relative mt-6 flex flex-col gap-4" data-rail>
            <div aria-hidden="true" className="rail start-[14px]">
              <div className="rail-fill" />
            </div>
            {c.aside.steps.map((s, i) => (
              <li key={s} className="relative flex gap-4" data-step>
                <span className="label relative z-10 inline-grid size-7 shrink-0 place-items-center rounded-full bg-accent text-xs font-bold text-on-accent">{localizeDigits(i + 1, locale)}</span>
                <span className="pt-0.5 text-ink-2">{s}</span>
              </li>
            ))}
          </ol>
          <div className="mt-10 border-t border-border-2 pt-5">
            <h2 className="label text-xs font-semibold text-ink-3">{c.aside.linksTitle}</h2>
            <ul className="mt-3 flex flex-col gap-1">
              {links.map((l) => (
                <li key={l.href}>
                  <Link href={l.href} className="link-arrow">
                    {l.label}
                    <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={14} />
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      </div>
    </div>
  );
}
