import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import "./globals.css";
import { getDictionary } from "@/i18n";
import { defaultLocale, dirOf, isLocale, type Locale } from "@/lib/i18n";
import { jakarta, vazirmatn } from "@/lib/fonts";
import { Header } from "@/components/site/Header";
import { Footer } from "@/components/site/Footer";
import { BottomTabBar } from "@/components/site/BottomTabBar";
import { tabBarNav } from "@/components/site/nav";

/**
 * The 404 document for every unmatched URL. It must render the full <html>, because Next
 * skips the layouts for it. The language comes from the request path (set by proxy.ts).
 */
async function currentLocale(): Promise<Locale> {
  const h = await headers();
  const value = h.get("x-locale") ?? "";
  return isLocale(value) ? value : defaultLocale;
}

export async function generateMetadata(): Promise<Metadata> {
  const locale = await currentLocale();
  const dict = await getDictionary(locale);
  return { title: `${dict.notFound.title} | ${dict.titleSuffix}`, description: dict.notFound.text };
}

export default async function GlobalNotFound() {
  const locale = await currentLocale();
  const dict = await getDictionary(locale);
  return (
    <html lang={locale} dir={dirOf(locale)} className={`${vazirmatn.variable} ${jakarta.variable}`}>
      <body className="page-body">
        <a href="#main" className="skip-link">
          {dict.common.skipToContent}
        </a>
        <Header locale={locale} dict={dict} />
        <main id="main" tabIndex={-1} className="outline-none">
          <section className="container-x py-20 md:py-28">
            <div className="mx-auto max-w-xl text-center">
              <p className="eyebrow justify-center">404</p>
              <h1 className="mt-3 text-3xl font-bold md:text-5xl">{dict.notFound.title}</h1>
              <p className="mt-4 text-lg text-ink-2">{dict.notFound.text}</p>
              <div className="mt-8 flex flex-wrap justify-center gap-3">
                <Link href={`/${locale}`} className="btn btn-primary">
                  {dict.notFound.home}
                </Link>
                <Link href={`/${locale}/contact`} className="btn btn-secondary">
                  {dict.notFound.contact}
                </Link>
              </div>
            </div>
          </section>
        </main>
        <Footer locale={locale} dict={dict} />
        <BottomTabBar locale={locale} items={tabBarNav(dict)} label={dict.nav.home} />
      </body>
    </html>
  );
}
