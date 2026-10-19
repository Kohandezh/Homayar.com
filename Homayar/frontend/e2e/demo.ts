import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
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

/** The localStorage key the app keeps the console language in (`src/lib/i18n.tsx`). */
export const LANG_STORAGE_KEY = "homayar_lang";

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

const E2E_DIR = path.dirname(fileURLToPath(import.meta.url));

/** The gitignored folder the setup project (`auth.setup.ts`) writes sessions to. */
export const AUTH_DIR = path.join(E2E_DIR, ".auth");

/** A role's saved session inside `AUTH_DIR`, written by the setup project. */
export const authFile = (role: DemoRole) => path.join(AUTH_DIR, `${role}.json`);

/** The parts of Playwright's saved storage state (`browserContext.storageState()`). */
interface SavedAuthState {
  cookies: {
    name: string;
    value: string;
    domain: string;
    path: string;
    expires: number;
    httpOnly: boolean;
    secure: boolean;
    sameSite: "Strict" | "Lax" | "None";
  }[];
  origins: { origin: string; localStorage: { name: string; value: string }[] }[];
}

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

/**
 * Sign in through the real form; the session cookie comes from the backend.
 * Only `auth.setup.ts` calls this: the backend limits sign-ins (20 per IP and
 * 10 per account in 15 minutes), so a run signs each role in exactly once
 * (HOM-209) and the specs continue those sessions with `resumeSession`.
 */
export async function signIn(page: Page, role: DemoRole, lang: Lang = "en") {
  await chooseLanguage(page, lang);
  const copy = SIGN_IN_COPY[lang];
  await page.goto(consolePath("/login"));
  await page.getByLabel(copy.email).fill(demoEmail(role));
  await page.getByLabel(copy.password, { exact: true }).fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: copy.submit, exact: true }).click();
  await expect(page).not.toHaveURL(/\/login(\?|$)/);
}

/**
 * Continue, on this page's context, the session the setup project saved for
 * `role`, and pick the language. This is how specs get a signed-in page; the
 * saved state carries the `homayar_csrf` cookie and `homayar_workspace_id`
 * but never `homayar_lang` (the setup strips it, so the language stays per
 * test).
 *
 * The saved localStorage is seeded once per page, on its first load. After
 * that the app owns the values: it removes `homayar_workspace_id` on purpose
 * (for example when access is lost), and the seed must not put it back. The
 * specs open one page per context, so for them this matches what Playwright's
 * own `storageState` does when a context starts; a test that opened a second
 * page in one context would seed that page again. `chooseLanguage` still only
 * fills an empty slot, so a test that switches language in the browser keeps
 * its choice across reloads.
 */
export async function resumeSession(page: Page, role: DemoRole, lang: Lang = "en") {
  await chooseLanguage(page, lang);
  const state = JSON.parse(await readFile(authFile(role), "utf-8")) as SavedAuthState;
  await page.context().addCookies(state.cookies);
  await page.addInitScript((origins) => {
    const saved = origins.find(({ origin }) => origin === window.location.origin);
    if (!saved) return;
    // The marker lives in `sessionStorage`, which a navigation in the same
    // page keeps, so exactly one navigation per page seeds.
    try {
      if (window.sessionStorage.getItem("homayar_e2e_seeded") !== null) return;
      window.sessionStorage.setItem("homayar_e2e_seeded", "1");
    } catch {
      /* storage blocked: the app re-fetches what it needs */
    }
    for (const { name, value } of saved.localStorage) {
      try {
        window.localStorage.setItem(name, value);
      } catch {
        /* storage blocked: the app re-fetches what it needs */
      }
    }
  }, state.origins);
}
