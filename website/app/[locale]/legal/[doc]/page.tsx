import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale, locales } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { Chip, Notice } from "@/components/ui/Cards";

const docs = ["privacy", "terms", "gdpr"] as const;
type Doc = (typeof docs)[number];

export const dynamicParams = false;

export function generateStaticParams() {
  return locales.flatMap((locale) => docs.map((doc) => ({ locale, doc })));
}

function isDoc(value: string): value is Doc {
  return (docs as readonly string[]).includes(value);
}

export async function generateMetadata({ params }: PageProps<"/[locale]/legal/[doc]">): Promise<Metadata> {
  const { locale, doc } = await params;
  if (!isLocale(locale) || !isDoc(doc)) return {};
  const dict = await getDictionary(locale);
  const page = dict.legal[doc];
  return pageMetadata({ locale, path: `/legal/${doc}`, title: page.meta.title, description: page.meta.description, siteName: dict.brand });
}

export default async function LegalPage({ params }: PageProps<"/[locale]/legal/[doc]">) {
  const { locale, doc } = await params;
  if (!isLocale(locale) || !isDoc(doc)) notFound();
  const dict = await getDictionary(locale);
  const page = dict.legal[doc];

  return (
    <div className="relative">
      <div className="container-x min-h-[60svh] py-14 md:py-20">
      <div className="mx-auto max-w-3xl">
        <div className="flex items-center gap-3" data-reveal>
          <p className="eyebrow">{dict.footer.legal}</p>
          <Chip tone="accent" live>{dict.common.inProgress}</Chip>
        </div>
        <h1 className="display mt-5" data-split>{page.title}</h1>
        <div className="mt-8">
          <Notice title={dict.common.inProgress}>{page.notice}</Notice>
        </div>
        {"site" in page ? (
          <section className="mt-8" data-reveal>
            <h2 className="text-xl font-bold">{page.site.title}</h2>
            <p className="mt-3 text-ink-2">{page.site.text}</p>
          </section>
        ) : null}
        <div className="mt-8 flex flex-wrap gap-3" data-reveal>
          <Link href={`/${locale}/contact`} className="btn btn-primary">
            {dict.nav.contact}
          </Link>
          <Link href={`/${locale}/security`} className="btn btn-ghost">
            {dict.nav.security}
          </Link>
        </div>
      </div>
      </div>
    </div>
  );
}
