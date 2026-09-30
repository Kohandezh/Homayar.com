import type { Metadata, Viewport } from "next";
import { notFound } from "next/navigation";
import "../globals.css";
import { getDictionary } from "@/i18n";
import { dirOf, isLocale, locales } from "@/lib/i18n";
import { jakarta, vazirmatn } from "@/lib/fonts";
import { themeScript } from "@/lib/theme";
import { Header } from "@/components/site/Header";
import { Footer } from "@/components/site/Footer";
import { BottomTabBar } from "@/components/site/BottomTabBar";
import { PageMotion } from "@/components/motion/PageMotion";
import { Stage } from "@/components/scene/Stage";
import { PosterDefs } from "@/components/scene/PosterDefs";
import { TransitionLayer } from "@/components/site/TransitionLayer";
import { tabBarNav } from "@/components/site/nav";

export function generateStaticParams() {
  return locales.map((locale) => ({ locale }));
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0a161a" },
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
  ],
  colorScheme: "dark light",
};

export async function generateMetadata({ params }: LayoutProps<"/[locale]">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return {
    title: { default: dict.home.meta.title, template: `%s | ${dict.titleSuffix}` },
    description: dict.home.meta.description,
    applicationName: dict.brand,
  };
}

export default async function LocaleLayout({ children, params }: LayoutProps<"/[locale]">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);

  return (
    <html lang={locale} dir={dirOf(locale)} className={`${vazirmatn.variable} ${jakarta.variable}`} suppressHydrationWarning>
      <body className="page-body">
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <a href="#main" className="skip-link">
          {dict.common.skipToContent}
        </a>
        <PosterDefs />
        <Stage rtl={locale === "fa"} />
        <div className="site-layer">
          <Header locale={locale} dict={dict} />
          <main id="main" tabIndex={-1} className="outline-none">
            <PageMotion locale={locale}>{children}</PageMotion>
          </main>
          <Footer locale={locale} dict={dict} />
        </div>
        <BottomTabBar locale={locale} items={tabBarNav(dict)} label={dict.nav.home} />
        <TransitionLayer locale={locale} loadingLabel={dict.loader.loading} brand={dict.brand} />
      </body>
    </html>
  );
}
