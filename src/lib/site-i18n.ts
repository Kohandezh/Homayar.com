import type { Locale } from "@/lib/content";
import { NAV_LINKS, FOOTER_GROUPS } from "@/lib/site";

export type NavItem = { href: string; label: string };
export type FooterGroup = { title: string; links: NavItem[] };

// Persian labels, keyed by the same hrefs so routing never drifts from copy.
const FA_NAV_LABELS: Record<string, string> = {
  "/platform": "پلتفرم",
  "/ai-agents": "عامل‌های هوشمند",
  "/solutions": "راهکارها",
  "/pricing": "تعرفه‌ها",
  "/catalog": "کاتالوگ",
  "/case-studies": "نمونه‌کارها",
  "/blog": "وبلاگ",
  "/about": "درباره ما",
  "/contact": "تماس / درخواست دمو",
  "/faq": "پرسش‌های متداول",
  "/privacy": "حریم خصوصی",
  "/terms": "شرایط استفاده",
};

const FA_FOOTER_TITLES: Record<string, string> = {
  Product: "محصول",
  Company: "شرکت",
  Resources: "منابع",
};

// English is canonical (defined in site.ts); fa overrides by href, every other
// locale falls back to the English chrome.
function localizeNav(
  items: readonly { href: string; label: string }[],
  locale: Locale,
): NavItem[] {
  return items.map((item) => ({
    href: item.href,
    label: locale === "fa" ? FA_NAV_LABELS[item.href] ?? item.label : item.label,
  }));
}

/** Route-nav links localized for the given locale. */
export function navLinks(locale: Locale): NavItem[] {
  return localizeNav(NAV_LINKS, locale);
}

/** Footer link groups localized for the given locale. */
export function footerGroups(locale: Locale): FooterGroup[] {
  return FOOTER_GROUPS.map((g) => ({
    title: locale === "fa" ? FA_FOOTER_TITLES[g.title] ?? g.title : g.title,
    links: localizeNav(g.links, locale),
  }));
}
