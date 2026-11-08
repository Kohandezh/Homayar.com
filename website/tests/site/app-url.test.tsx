// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { beforeAll, afterEach, describe, expect, it, vi } from "vitest";
import type { RenderResult } from "@testing-library/react";
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import type { Dictionary } from "@/i18n/en";

// jsdom has HTMLDialogElement.open but not showModal/close; MobileMenu calls both.
beforeAll(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.removeAttribute("open");
    };
  }
});

// ActiveLink, LangSwitcher and MobileMenu call usePathname; there is no Next.js
// runtime in jsdom, so next/navigation is mocked here.
vi.mock("next/navigation", () => ({
  usePathname: () => "/en",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const APP_HREF = "https://app.homayar.com:8080";
const FALLBACK_HREF = "/app";
type Locale = "en" | "fa";

// Vite statically rewrites `new URL(<literal>, import.meta.url)` into a dev-server
// URL, which is not a file: URL. Reading the base into a variable first keeps the
// normal runtime resolution, relative to this test file.
const testFileUrl = import.meta.url;
const workflow = readFileSync(
  fileURLToPath(new URL("../../../.github/workflows/deploy-website.yml", testFileUrl)),
  "utf8",
);

function stepBlock(stepName: string): string {
  const start = workflow.indexOf(`- name: ${stepName}`);
  if (start === -1) throw new Error(`step "${stepName}" not found in deploy-website.yml`);
  const next = workflow.indexOf("\n      - name: ", start + 1);
  return workflow.slice(start, next === -1 ? undefined : next + 1);
}

// Re-evaluates lib/site.ts (and everything that imports it) with the stubbed
// environment, so appUrl picks up the value that was set when modules loaded.
async function renderChrome(appEnv: string | undefined, locale: Locale) {
  vi.stubEnv("NEXT_PUBLIC_APP_URL", appEnv);
  vi.resetModules();
  const [{ Header }, { Footer }] = await Promise.all([
    import("@/components/site/Header"),
    import("@/components/site/Footer"),
  ]);
  const dict = (locale === "en" ? await import("@/i18n/en") : await import("@/i18n/fa")).default as Dictionary;
  return {
    dict,
    header: render(<Header locale={locale} dict={dict} />),
    footer: render(<Footer locale={locale} dict={dict} />),
  };
}

function expectAppLinks(dict: Dictionary, header: RenderResult, footer: RenderResult, want: string) {
  expect((header.getByTestId("login-link") as HTMLAnchorElement).getAttribute("href")).toBe(want);
  expect((header.getByTestId("start-link") as HTMLAnchorElement).getAttribute("href")).toBe(want);

  fireEvent.click(header.getByRole("button", { name: dict.common.menu }));
  const dialog = header.container.querySelector("dialog#mobile-menu");
  if (!dialog) throw new Error("mobile menu dialog not rendered");
  expect((dialog as HTMLDialogElement).open).toBe(true);
  const menu = within(dialog as HTMLElement);
  expect((menu.getByRole("link", { name: dict.common.login }) as HTMLAnchorElement).getAttribute("href")).toBe(want);
  expect((menu.getByRole("link", { name: dict.common.start }) as HTMLAnchorElement).getAttribute("href")).toBe(want);

  expect((within(footer.container).getByRole("link", { name: dict.common.login }) as HTMLAnchorElement).getAttribute("href")).toBe(want);
}

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("deploy-website.yml sets the app URL at build time (AC1)", () => {
  it("the Build for Cloudflare Workers step sets NEXT_PUBLIC_APP_URL to the exact app URL", () => {
    const step = stepBlock("Build for Cloudflare Workers");
    expect(step).toMatch(/^\s+NEXT_PUBLIC_APP_URL:[ \t]*https:\/\/app\.homayar\.com:8080[ \t]*$/m);
    expect(step).not.toMatch(/NEXT_PUBLIC_APP_URL:[ \t]*https:\/\/app\.homayar\.com:8080\/[^\n]*/);
  });

  it("the same step keeps the Turnstile site key and its guard (control)", () => {
    const step = stepBlock("Build for Cloudflare Workers");
    expect(step).toContain("NEXT_PUBLIC_TURNSTILE_SITE_KEY: ${{ vars.TURNSTILE_SITE_KEY }}");
    expect(step).toContain("Missing Turnstile site key");
  });
});

describe.each(["en", "fa"] as Locale[])("app links with the production app URL (%s)", (locale) => {
  it("header, mobile menu and footer all link to https://app.homayar.com:8080 (AC2)", async () => {
    const { dict, header, footer } = await renderChrome(APP_HREF, locale);
    expectAppLinks(dict, header, footer, APP_HREF);
  });
});

const ac3Cases: { name: string; appEnv: string | undefined; want: string }[] = [
  { name: "unset", appEnv: undefined, want: FALLBACK_HREF },
  { name: "only spaces", appEnv: "   ", want: FALLBACK_HREF },
  { name: "spaces around the URL", appEnv: `  ${APP_HREF}  `, want: APP_HREF },
];

describe.each(ac3Cases)("app links with NEXT_PUBLIC_APP_URL $name (AC3)", ({ appEnv, want }) => {
  it.each(["en", "fa"] as Locale[])("%s: links use the fallback or the trimmed value", async (locale) => {
    const { dict, header, footer } = await renderChrome(appEnv, locale);
    expectAppLinks(dict, header, footer, want);
  });
});
