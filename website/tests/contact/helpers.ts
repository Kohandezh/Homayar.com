import type { ContactInput } from "@/lib/contact-schema";
import type { ContactEnv } from "@/lib/contact/handle";
import { createFakeD1, type FakeD1 } from "./fake-d1";

export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
export const WEBHOOK_URL = "https://hooks.example.com/alert";
export const NOW = new Date("2026-10-23T12:00:00.000Z");

export const validBody = {
  name: "Sara Ahmadi",
  email: "sara@example.org",
  company: "Example Telecom",
  role: "qa",
  deployment: "self-hosted",
  minutes: "50k-250k",
  phone: "+98 21 1234-5678",
  message: "We run 40 agents on Issabel.",
  locale: "en",
} satisfies ContactInput;

export interface SentMail {
  to: string;
  from: { email: string; name: string };
  replyTo: string;
  subject: string;
  text: string;
  html: string;
}

export interface Harness {
  env: ContactEnv;
  db: FakeD1;
  mails: SentMail[];
  fetchCalls: { url: string; init: RequestInit }[];
  /** What the next send does. "ok" sends, an Error is thrown. */
  sendOutcome: "ok" | Error;
  /** What siteverify answers. */
  siteverify: { status: number; body: unknown } | "throw";
  webhook: "ok" | "throw" | number;
  fetch: typeof fetch;
  now: () => Date;
}

export function createHarness(overrides: Partial<ContactEnv> = {}): Harness {
  const db = createFakeD1();
  const h: Harness = {
    db,
    mails: [],
    fetchCalls: [],
    sendOutcome: "ok",
    siteverify: { status: 200, body: { success: true } },
    webhook: "ok",
    now: () => NOW,
    env: undefined as unknown as ContactEnv,
    fetch: (async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      h.fetchCalls.push({ url, init });
      if (url === SITEVERIFY_URL) {
        if (h.siteverify === "throw") throw new TypeError("fetch failed");
        return new Response(JSON.stringify(h.siteverify.body), { status: h.siteverify.status });
      }
      if (url === WEBHOOK_URL) {
        if (h.webhook === "throw") throw new TypeError("fetch failed");
        return new Response("ok", { status: h.webhook === "ok" ? 200 : h.webhook });
      }
      throw new Error(`unexpected fetch to ${url}`);
    }) as typeof fetch,
  };
  h.env = {
    CONTACT_DB: db as unknown as ContactEnv["CONTACT_DB"],
    CONTACT_EMAIL: {
      async send(message: unknown) {
        if (h.sendOutcome !== "ok") throw h.sendOutcome;
        h.mails.push(message as SentMail);
        return { messageId: `<m${h.mails.length}@homayar.com>` };
      },
    },
    CONTACT_TO: "owner@example.com",
    CONTACT_FROM: "website@homayar.com",
    TURNSTILE_SECRET_KEY: "1x0000000000000000000000000000000AA",
    CONTACT_ALERT_WEBHOOK_URL: WEBHOOK_URL,
    ...overrides,
  };
  return h;
}

export function contactRequest(body: unknown, headers: Record<string, string> = {}): Request {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Request("https://homayar.com/api/contact", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: text,
  });
}

export function withToken(body: Record<string, unknown> = validBody, token = "XXXX.DUMMY.TOKEN.XXXX") {
  return { ...body, turnstileToken: token };
}

export function rows(h: Harness): Record<string, unknown>[] {
  try {
    return h.db.sqlite.prepare("SELECT * FROM contact_requests ORDER BY created_at").all().map((r) => ({ ...r }));
  } catch {
    return [];
  }
}

export function siteverifyCalls(h: Harness) {
  return h.fetchCalls.filter((c) => c.url === SITEVERIFY_URL);
}

export function webhookCalls(h: Harness) {
  return h.fetchCalls.filter((c) => c.url === WEBHOOK_URL);
}
