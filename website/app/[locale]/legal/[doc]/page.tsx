import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale, locales } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { Notice } from "@/components/ui/Cards";

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

/** Honest placeholder: says the document is being prepared, in the page language, with no commitment text. */
export default async function LegalPage({ params }: PageProps<"/[locale]/legal/[doc]">) {
  const { locale, doc } = await params;
  if (!isLocale(locale) || !isDoc(doc)) notFound();
  const dict = await getDictionary(locale);
  const page = dict.legal[doc];

  return (
    <div className="container-x py-12 md:py-16">
      <div className="mx-auto max-w-3xl">
        <p className="eyebrow">{dict.footer.legal}</p>
        <h1 className="mt-3 text-3xl font-bold md:text-5xl">{page.title}</h1>
        <div className="mt-8">
          <Notice title={dict.common.inProgress}>{page.notice}</Notice>
        </div>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link href={`/${locale}/contact`} className="btn btn-primary">
            {dict.nav.contact}
          </Link>
          <Link href={`/${locale}/security`} className="btn btn-secondary">
            {dict.nav.security}
          </Link>
        </div>
      </div>
    </div>
  );
}
