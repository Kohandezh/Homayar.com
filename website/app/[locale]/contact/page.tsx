import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { ContactForm } from "@/components/site/ContactForm";

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
    <div className="container-x grid gap-10 py-12 md:py-16 lg:grid-cols-[1.2fr_1fr] lg:gap-16">
      <div>
        <p className="eyebrow">{c.hero.eyebrow}</p>
        <h1 className="mt-3 text-3xl font-bold md:text-5xl">{c.hero.title}</h1>
        <p className="mt-4 max-w-xl text-lg text-ink-2">{c.hero.text}</p>
        <div className="card mt-8 p-5 md:p-8">
          <ContactForm copy={c.form} locale={locale} />
        </div>
      </div>
      <aside className="lg:sticky lg:top-[calc(var(--header-h)+1.5rem)] lg:self-start">
        <div className="rounded-[20px] bg-ink-teal p-6 text-cream md:p-8">
          <h2 className="text-xl font-bold">{c.aside.title}</h2>
          <ol className="mt-5 flex flex-col gap-4">
            {c.aside.steps.map((s, i) => (
              <li key={s} className="flex gap-3">
                <span className="inline-grid size-7 shrink-0 place-items-center rounded-full bg-accent text-sm font-bold text-ink">{localizeDigits(i + 1, locale)}</span>
                <span className="text-cream/90">{s}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className="card mt-4 p-6">
          <h2 className="font-semibold">{c.aside.linksTitle}</h2>
          <ul className="mt-3 flex flex-col gap-1">
            {links.map((l) => (
              <li key={l.href}>
                <Link href={l.href} className="inline-flex min-h-11 items-center font-medium text-primary hover:underline">
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}
