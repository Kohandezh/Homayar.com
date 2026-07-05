import type { Metadata } from "next";
import { Inter, Space_Grotesk, Vazirmatn } from "next/font/google";
import "./globals.css";
import { CartProvider } from "@/lib/cart";
import { LocaleProvider } from "@/lib/locale";
import { SiteChrome } from "@/components/site-chrome";
import { CONTENT } from "@/lib/content";
import { SITE } from "@/lib/site";

// Persian (default) — primary UI font, also used for Latin glyphs inside FA text.
const vazirmatn = Vazirmatn({
  variable: "--font-vazir",
  subsets: ["arabic", "latin"],
  display: "swap",
});

// English UI font.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const spaceGrotesk = Space_Grotesk({
  variable: "--font-space-grotesk",
  subsets: ["latin"],
  display: "swap",
});

const fa = CONTENT.fa;

export const metadata: Metadata = {
  metadataBase: new URL(SITE.url),
  title: {
    default: fa.htmlTitle,
    template: `%s | ${fa.footer.brand}`,
  },
  description: fa.metaDescription,
  keywords: [
    "هوش مکالمه",
    "تحلیل هوشمند تماس",
    "Conversation Intelligence",
    "کنترل کیفیت مرکز تماس",
    "تحلیل مکالمات",
    "همایار",
    "کهن سیستم فردا",
  ],
  openGraph: {
    type: "website",
    siteName: fa.footer.brand,
    title: fa.htmlTitle,
    description: fa.metaDescription,
    url: SITE.url,
    locale: "fa_IR",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: fa.footer.brand }],
  },
  twitter: {
    card: "summary_large_image",
    title: fa.htmlTitle,
    description: fa.metaDescription,
    images: ["/og.png"],
  },
  robots: { index: true, follow: true },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="fa"
      dir="rtl"
      suppressHydrationWarning
      className={`${vazirmatn.variable} ${inter.variable} ${spaceGrotesk.variable} h-full antialiased`}
    >
      <head>
        {/* Set lang/dir before first paint so no visitor sees a flash of the
            default RTL/Persian layout: use the saved choice for returning
            visitors, else the browser language for first-timers. Falls back to
            fa. Mirrors the DIR map in lib/content (fa & ar are RTL). */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var s=['fa','en','de','ar','fr','es'];var l=localStorage.getItem('homayar-locale');if(!l||s.indexOf(l)<0){var n=(navigator.language||'').slice(0,2).toLowerCase();l=s.indexOf(n)>-1?n:'fa';}var rtl=l==='fa'||l==='ar';var e=document.documentElement;e.lang=l;e.dir=rtl?'rtl':'ltr';}catch(e){}})();",
          }}
        />
        {/* Organization structured data — helps search engines recognize the
            company entity and enables richer results. */}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "Organization",
              name: SITE.name,
              legalName: SITE.legalName,
              url: SITE.url,
              logo: `${SITE.url}/homayar-logo.png`,
              image: `${SITE.url}/og.png`,
              email: SITE.contactEmail,
              description: fa.metaDescription,
            }),
          }}
        />
      </head>
      <body className="flex min-h-screen flex-col">
        <LocaleProvider>
          <CartProvider>
            <SiteChrome>{children}</SiteChrome>
          </CartProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
