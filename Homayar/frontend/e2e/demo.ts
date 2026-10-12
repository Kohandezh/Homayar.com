import { expect, type Page } from "@playwright/test";

/**
 * The demo workspace seed (HOM-175) is the data these tests run against. The
 * values below mirror `backend/database/demo_seed.py`; change them together.
 */
export const DEMO_PASSWORD = "Homayar-Demo-1405";
export const DEMO_EMAIL_DOMAIN = "demo.example.com";
export const DEMO_WORKSPACE_NAME = "شرکت نمونه همایار";

export type DemoRole = "owner" | "admin" | "member" | "viewer";
export type Lang = "fa" | "en";

export const DEMO_DISPLAY_NAMES: Record<DemoRole, string> = {
  owner: "سارا احمدی",
  admin: "Reza Karimi",
  member: "مریم حسینی",
  viewer: "Ali Rahimi",
};

export const demoEmail = (role: DemoRole) => `${role}@${DEMO_EMAIL_DOMAIN}`;

export const API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";
export const SMTP_PORT = Number(process.env.E2E_SMTP_PORT ?? 1025);

/** Vite serves the console under `/app/`, and the router uses it as its base. */
export const consolePath = (path: string) => `/app${path}`;

const SIGN_IN_COPY = {
  en: { email: "Email address", password: "Password", submit: "Sign in" },
  fa: { email: "نشانی ایمیل", password: "گذرواژه", submit: "ورود" },
} as const;

/**
 * Pick the console language before the app boots. `I18nProvider` reads this
 * key on its first render, so setting it afterwards would re-render the whole
 * tree mid-test.
 *
 * The script runs on every navigation, so it only seeds an empty slot: a
 * language the test switched to in the browser survives a reload.
 */
export async function chooseLanguage(page: Page, lang: Lang) {
  await page.addInitScript((value) => {
    try {
      if (window.localStorage.getItem("homayar_lang") === null) {
        window.localStorage.setItem("homayar_lang", value);
      }
    } catch {
      /* storage blocked: the app falls back to Persian */
    }
  }, lang);
}

/** Sign in through the real form; the session cookie comes from the backend. */
export async function signIn(page: Page, role: DemoRole, lang: Lang = "en") {
  await chooseLanguage(page, lang);
  const copy = SIGN_IN_COPY[lang];
  await page.goto(consolePath("/login"));
  await page.getByLabel(copy.email).fill(demoEmail(role));
  await page.getByLabel(copy.password, { exact: true }).fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: copy.submit, exact: true }).click();
  await expect(page).not.toHaveURL(/\/login(\?|$)/);
}
