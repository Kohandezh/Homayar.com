import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import "./globals.css";
import { getDictionary } from "@/i18n";
import { defaultLocale, dirOf, isLocale, type Locale } from "@/lib/i18n";
import { jakarta, vazirmatn } from "@/lib/fonts";
import { themeScript } from "@/lib/theme";
import { Header } from "@/components/site/Header";
import { Footer } from "@/components/site/Footer";
import { BottomTabBar } from "@/components/site/BottomTabBar";
import { tabBarNav } from "@/components/site/nav";
import { Stage } from "@/components/scene/Stage";
import { SceneBackdrop } from "@/components/scene/SceneBackdrop";
import { TransitionLayer } from "@/components/site/TransitionLayer";

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
    <html lang={locale} dir={dirOf(locale)} className={`${vazirmatn.variable} ${jakarta.variable}`} suppressHydrationWarning>
      <body className="page-body">
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <a href="#main" className="skip-link">
          {dict.common.skipToContent}
        </a>
        <Stage rtl={locale === "fa"} />
        <div className="site-layer">
          <Header locale={locale} dict={dict} />
          <main id="main" tabIndex={-1} className="outline-none">
            <section className="relative isolate overflow-hidden" data-scene="warp">
              <SceneBackdrop kind="warp" priority />
              <div className="scene-fade-bottom" aria-hidden="true" />
              <div className="container-x flex min-h-[70svh] items-center py-20">
                <div className="mx-auto flex max-w-xl flex-col items-center text-center">
                  <p className="chip chip-accent">404</p>
                  <h1 className="display mt-6">{dict.notFound.title}</h1>
                  <p className="mt-5 text-lg text-ink-2">{dict.notFound.text}</p>
                  <div className="mt-8 flex flex-wrap justify-center gap-3">
                    <Link href={`/${locale}`} className="btn btn-primary">
                      {dict.notFound.home}
                    </Link>
                    <Link href={`/${locale}/contact`} className="btn btn-ghost">
                      {dict.notFound.contact}
                    </Link>
                  </div>
                </div>
              </div>
            </section>
          </main>
          <Footer locale={locale} dict={dict} />
        </div>
        <BottomTabBar locale={locale} items={tabBarNav(dict)} label={dict.nav.home} />
        <TransitionLayer locale={locale} loadingLabel={dict.loader.loading} brand={dict.brand} />
      </body>
    </html>
  );
}
