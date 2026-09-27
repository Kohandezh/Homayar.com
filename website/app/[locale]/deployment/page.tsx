import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section } from "@/components/ui/Section";
import { CheckList } from "@/components/ui/Cards";
import { CtaBand } from "@/components/site/CtaBand";
import { Icon } from "@/components/ui/Icon";

export async function generateMetadata({ params }: PageProps<"/[locale]/deployment">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/deployment", title: dict.deployment.meta.title, description: dict.deployment.meta.description, siteName: dict.brand });
}

const optionIcons = ["cloud", "server", "layers"];

export default async function DeploymentPage({ params }: PageProps<"/[locale]/deployment">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const d = dict.deployment;

  return (
    <>
      <PageHero eyebrow={d.hero.eyebrow} title={d.hero.title} text={d.hero.text} />

      <Section>
        <div className="grid gap-5 lg:grid-cols-3">
          {d.options.map((o, i) => (
            <article key={o.id} id={o.id} className={`flex flex-col rounded-[20px] p-6 md:p-7 ${i === 1 ? "bg-ink-teal text-cream" : "card"}`}>
              <span className={`inline-grid size-12 place-items-center rounded-xl ${i === 1 ? "bg-cream/15 text-accent" : "bg-primary-soft text-primary"}`}>
                <Icon name={optionIcons[i]} size={24} />
              </span>
              <h2 className="mt-4 text-2xl font-bold">{o.name}</h2>
              <p className={`mt-1 ${i === 1 ? "text-cream/85" : "text-ink-2"}`}>{o.summary}</p>
              <div className="mt-5">
                <CheckList items={o.points} tone={i === 1 ? "dark" : "light"} />
              </div>
              <p className={`mt-6 rounded-xl p-3 text-sm ${i === 1 ? "bg-cream/10 text-cream/90" : "bg-sand text-ink-2"}`}>{o.fit}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section tone="sand">
        <h2 className="text-2xl font-bold md:text-3xl">{d.compare.title}</h2>
        <div className="mt-6 overflow-x-auto rounded-[16px] border border-border bg-surface">
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
                    <td key={i} className="text-ink-2">{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section>
        <div className="grid gap-6 rounded-[24px] border border-border bg-surface p-6 md:grid-cols-[1fr_1fr] md:items-center md:p-10">
          <h2 className="text-2xl font-bold md:text-3xl">{d.runtimeNote.title}</h2>
          <ul className="grid gap-3 sm:grid-cols-2">
            {d.runtimeNote.items.map((i) => (
              <li key={i} className="flex items-center gap-3 rounded-xl bg-sand px-4 py-3 font-medium">
                <span className="inline-grid size-7 shrink-0 place-items-center rounded-full bg-ink-teal text-cream"><Icon name="x" size={14} strokeWidth={2.5} /></span>
                {i}
              </li>
            ))}
          </ul>
        </div>
      </Section>

      <CtaBand locale={locale} dict={dict} />
    </>
  );
}
