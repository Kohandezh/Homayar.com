import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { Notice } from "@/components/ui/Cards";

export async function generateMetadata({ params }: PageProps<"/[locale]/transparency">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/transparency", title: dict.transparency.meta.title, description: dict.transparency.meta.description, siteName: dict.brand });
}

export default async function TransparencyPage({ params }: PageProps<"/[locale]/transparency">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const t = dict.transparency;

  return (
    <div className="container-x py-12 md:py-16">
      <div className="mx-auto max-w-3xl">
        <p className="eyebrow">{dict.footer.trust}</p>
        <h1 className="mt-3 text-3xl font-bold md:text-5xl">{t.title}</h1>
        <div className="mt-8">
          <Notice title={dict.common.inProgress}>{t.notice}</Notice>
        </div>
        <div className="mt-8">
          <Link href={`/${locale}/security`} className="btn btn-primary">
            {t.backLink}
          </Link>
        </div>
      </div>
    </div>
  );
}
