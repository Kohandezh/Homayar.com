import { contactSchema } from "../contact-schema";
import { reportProblem } from "./alert";
import type { D1Database } from "./d1";
import { deliver } from "./deliver";
import { insertPending, markFailed, markSent } from "./store";
import { verifyTurnstile } from "./turnstile";

export interface ContactEnv {
  CONTACT_DB: D1Database; // only prepare/bind/first/all/run are used
  CONTACT_EMAIL: { send(message: unknown): Promise<unknown> };
  CONTACT_TO?: string;
  CONTACT_FROM?: string;
  TURNSTILE_SECRET_KEY?: string;
  CONTACT_ALERT_WEBHOOK_URL?: string;
}

export interface ContactDeps {
  fetch?: typeof fetch; // used for siteverify and the alert webhook; default globalThis.fetch
  now?: () => Date; // default () => new Date()
}

const maxBodyBytes = 16 * 1024;

function reply(status: number, body: Record<string, unknown>): Response {
  return Response.json(body, { status });
}

/**
 * POST /api/contact. Order: size, JSON, fields, Turnstile, store, send.
 * The request is stored before any send, so a failed send never loses the lead:
 * the cron retry (retry.ts) sends it later.
 */
export async function handleContactRequest(request: Request, env: ContactEnv, deps: ContactDeps = {}): Promise<Response> {
  const doFetch = deps.fetch ?? globalThis.fetch;
  const now = deps.now ?? (() => new Date());

  const length = Number(request.headers.get("content-length") ?? 0);
  if (length > maxBodyBytes) return reply(413, { ok: false, error: "too_large" });

  const raw = await request.text();
  // A body sent without content-length gets the same limit.
  if (new TextEncoder().encode(raw).length > maxBodyBytes) return reply(413, { ok: false, error: "too_large" });

  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return reply(400, { ok: false, error: "invalid_json" });
  }

  const parsed = contactSchema.safeParse(body);
  if (!parsed.success) {
    const fields = [...new Set(parsed.error.issues.map((i) => String(i.path[0] ?? "body")))];
    return reply(400, { ok: false, error: "invalid_fields", fields });
  }

  const token = (body as { turnstileToken?: unknown }).turnstileToken;
  const turnstile = await verifyTurnstile(token, env.TURNSTILE_SECRET_KEY, request.headers.get("CF-Connecting-IP"), doFetch);
  if (turnstile === "unavailable") return reply(503, { ok: false, error: "turnstile_unavailable" });
  if (turnstile === "failed") return reply(403, { ok: false, error: "turnstile_failed" });

  // parsed.data holds only the schema fields, so the Turnstile token is never stored.
  const id = crypto.randomUUID();
  const receivedAt = now().toISOString();
  try {
    await insertPending(env.CONTACT_DB, id, receivedAt, JSON.stringify(parsed.data));
  } catch {
    await reportProblem(env, deps, { kind: "contact_store_failed", attempts: 0, error: "store_failed" });
    return reply(503, { ok: false, error: "store_failed" });
  }

  const outcome = await deliver(env, id, receivedAt, parsed.data);
  const attemptAt = now().toISOString();
  if (outcome.ok) {
    try {
      await markSent(env.CONTACT_DB, id, 1, attemptAt);
    } catch {
      // The mail went out. The row stays pending, and the retry would send it again
      // after 5 minutes: a duplicate mail is better than a lost lead.
      console.warn(`contact_update_failed ${JSON.stringify({ id })}`);
    }
    return reply(202, { ok: true, id, delivery: "sent" });
  }

  try {
    await markFailed(env.CONTACT_DB, id, "failed", 1, attemptAt, outcome.error);
  } catch {
    console.warn(`contact_update_failed ${JSON.stringify({ id })}`);
  }
  await reportProblem(env, deps, { kind: "contact_delivery_failed", id, attempts: 1, error: outcome.error });
  return reply(202, { ok: true, id, delivery: "queued" });
}
