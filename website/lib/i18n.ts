export const locales = ["fa", "en"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "fa";

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}

export function otherLocale(locale: Locale): Locale {
  return locale === "fa" ? "en" : "fa";
}

export function dirOf(locale: Locale): "rtl" | "ltr" {
  return locale === "fa" ? "rtl" : "ltr";
}

const persianDigits = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];

/** Converts ASCII digits to Persian digits. Used for numbers shown in Persian prose and mockups. */
export function localizeDigits(value: string | number, locale: Locale): string {
  const text = String(value);
  if (locale !== "fa") return text;
  return text.replace(/[0-9]/g, (d) => persianDigits[Number(d)]);
}
