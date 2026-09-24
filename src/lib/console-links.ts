import type { Locale } from "@/lib/content";

/**
 * Entry points from the public website into the product console.
 *
 * The console is a separate application (the Vite SPA in the product
 * repository) that nginx serves on the same origin under `/app/`
 * (infra/nginx/conf.d/homayar.conf). Links into it must be plain `<a>` tags:
 * `<Link>` would try to prefetch and soft-navigate to a route this Next app
 * does not own. `NEXT_PUBLIC_CONSOLE_URL` is inlined at build time; set it only
 * when the website is deployed without the `/app/` proxy.
 */
export const CONSOLE_URL = (process.env.NEXT_PUBLIC_CONSOLE_URL || "/app").replace(
  /\/+$/,
  "",
);

export type ConsoleEntry = "login" | "register";

export function consoleHref(entry: ConsoleEntry): string {
  return `${CONSOLE_URL}/${entry}`;
}

const LABELS: Record<Locale, Record<ConsoleEntry, string>> = {
  fa: { login: "ورود", register: "ساخت حساب" },
  en: { login: "Sign in", register: "Create account" },
  de: { login: "Anmelden", register: "Konto erstellen" },
  ar: { login: "تسجيل الدخول", register: "إنشاء حساب" },
  fr: { login: "Se connecter", register: "Créer un compte" },
  es: { login: "Iniciar sesión", register: "Crear cuenta" },
};

/** Localized labels for the console entry points. */
export function consoleLabels(locale: Locale): Record<ConsoleEntry, string> {
  return LABELS[locale] ?? LABELS.en;
}
