import { mkdir, rm, writeFile } from "node:fs/promises";
import { test } from "@playwright/test";
import { AUTH_DIR, authFile, LANG_STORAGE_KEY, signIn, type DemoRole, type Lang } from "./demo";

/**
 * One form sign-in per demo role per run (HOM-209), so the whole suite stays
 * far below the backend's login rate limit (20 per IP, 10 per account, in 15
 * minutes). Each sign-in goes through the real form (the en one and, for
 * one role, the fa one, so both stay covered) and the session is saved with
 * Playwright `storageState` for the specs to resume (`resumeSession` in
 * `demo.ts`). The `chromium` project lists this project as a dependency, so
 * these tests always run first.
 */

/**
 * The member signs in through the Persian form: the suite covers both login
 * forms, and this way that coverage needs no extra session.
 */
const ROLES: { role: DemoRole; lang: Lang }[] = [
  { role: "owner", lang: "en" },
  { role: "admin", lang: "en" },
  { role: "member", lang: "fa" },
  { role: "viewer", lang: "en" },
];

test.beforeAll(async () => {
  // A run never reuses a session from an earlier run: access tokens live
  // 15 minutes and a replayed refresh token revokes the session, so the
  // state is remade here, at the start of every run, and thrown away with
  // the run (the folder is gitignored).
  await rm(AUTH_DIR, { recursive: true, force: true });
  await mkdir(AUTH_DIR, { recursive: true });
});

for (const { role, lang } of ROLES) {
  test(`signs the ${role} in through the ${lang} form and saves the session`, async ({
    page,
  }) => {
    await signIn(page, role, lang);

    const state = await page.context().storageState();
    // The language must stay per test: `chooseLanguage` only fills an empty
    // slot, so the saved state may not carry `homayar_lang`. Everything else
    // is kept: the specs need the `homayar_csrf` cookie and the
    // `homayar_workspace_id` in localStorage.
    for (const origin of state.origins) {
      origin.localStorage = origin.localStorage.filter((item) => item.name !== LANG_STORAGE_KEY);
    }
    await writeFile(authFile(role), JSON.stringify(state, null, 2));
  });
}
