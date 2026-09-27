import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { Chip, Notice } from "@/components/ui/Cards";

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
    <div className="container-x py-14 md:py-20">
      <div className="mx-auto max-w-3xl">
        <div className="flex items-center gap-3" data-reveal>
          <p className="eyebrow">{dict.footer.trust}</p>
          <Chip tone="amber" live>{dict.common.inProgress}</Chip>
        </div>
        <h1 className="display mt-5" data-split>{t.title}</h1>
        <div className="mt-8">
          <Notice title={dict.common.inProgress}>{t.notice}</Notice>
        </div>
        <div className="mt-8" data-reveal>
          <Link href={`/${locale}/security`} className="btn btn-primary">
            {t.backLink}
          </Link>
        </div>
      </div>
    </div>
  );
}
