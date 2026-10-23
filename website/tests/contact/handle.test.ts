import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { handleContactRequest } from "@/lib/contact/handle";
import {
  NOW,
  contactRequest,
  createHarness,
  rows,
  siteverifyCalls,
  validBody,
  webhookCalls,
  withToken,
  type Harness,
} from "./helpers";

const personal = [validBody.name, validBody.email, validBody.phone, validBody.company, validBody.message];

let errors: string[];
beforeEach(() => {
  errors = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    errors.push(args.map(String).join(" "));
  });
});
afterEach(() => vi.restoreAllMocks());

async function post(h: Harness, body: unknown, headers: Record<string, string> = {}) {
  const res = await handleContactRequest(contactRequest(body, headers), h.env, { fetch: h.fetch, now: h.now });
  return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

function expectNoPersonalData(text: string) {
  for (const value of personal) expect(text).not.toContain(value);
}

describe("POST /api/contact: success (A1)", () => {
  it("stores the row, sends one mail to CONTACT_TO and answers 202 sent", async () => {
    const h = createHarness();
    const { status, json } = await post(h, withToken());
    expect(status).toBe(202);
    expect(json).toEqual({ ok: true, id: expect.any(String), delivery: "sent" });

    const all = rows(h);
    expect(all).toHaveLength(1);
    expect(all[0]).toMatchObject({
      id: json.id,
      status: "sent",
      attempts: 1,
      created_at: NOW.toISOString(),
      last_attempt_at: NOW.toISOString(),
      sent_at: NOW.toISOString(),
      last_error: null,
    });

    expect(h.mails).toHaveLength(1);
    expect(h.mails[0].to).toBe("owner@example.com");
    expect(h.mails[0].replyTo).toBe(validBody.email);
    expect(h.mails[0].from).toEqual({ email: "website@homayar.com", name: "Homayar website" });
    expect(webhookCalls(h)).toHaveLength(0);
  });

  it("writes the row before it sends", async () => {
    const h = createHarness();
    let rowsAtSend = -1;
    h.env.CONTACT_EMAIL = {
      async send() {
        rowsAtSend = rows(h).length;
        return {};
      },
    };
    await post(h, withToken());
    expect(rowsAtSend).toBe(1);
  });

  it("never stores the Turnstile token in the payload", async () => {
    const h = createHarness();
    await post(h, withToken(validBody, "SECRET-TOKEN-VALUE"));
    const payload = String(rows(h)[0].payload);
    expect(payload).not.toContain("SECRET-TOKEN-VALUE");
    expect(JSON.parse(payload)).toMatchObject({ email: validBody.email, minutes: "50k-250k" });
  });
});

describe("POST /api/contact: delivery failures (A2, A3)", () => {
  it("send throws: 202 queued, row failed with a fixed code, one alert without personal data", async () => {
    const h = createHarness();
    h.sendOutcome = new Error(`email to ${validBody.email} not allowed`);
    const { status, json } = await post(h, withToken());
    expect(status).toBe(202);
    expect(json).toEqual({ ok: true, id: expect.any(String), delivery: "queued" });

    const [row] = rows(h);
    expect(row).toMatchObject({ status: "failed", attempts: 1, last_attempt_at: NOW.toISOString(), sent_at: null });
    expect(row.last_error).toBe("send_failed");

    const alerts = webhookCalls(h);
    expect(alerts).toHaveLength(1);
    expect(alerts[0].init.method).toBe("POST");
    expect(new Headers(alerts[0].init.headers).get("Content-Type")).toBe("application/json");
    const text = JSON.parse(String(alerts[0].init.body)).text as string;
    expect(text).toContain(String(json.id));
    expect(text).toContain("send_failed");
    expectNoPersonalData(text);

    expect(errors).toHaveLength(1);
    expect(errors[0].startsWith("contact_delivery_failed")).toBe(true);
    expect(errors[0]).toContain(String(json.id));
    expectNoPersonalData(errors[0]);
  });

  it("keeps a Cloudflare error code but never the error message", async () => {
    const h = createHarness();
    h.sendOutcome = Object.assign(new Error(`rejected ${validBody.email}`), { code: "E_SENDER_NOT_VERIFIED" });
    await post(h, withToken());
    expect(rows(h)[0].last_error).toBe("E_SENDER_NOT_VERIFIED");
  });

  it("uses the error name for a typed error", async () => {
    const h = createHarness();
    h.sendOutcome = new TypeError(`bad ${validBody.email}`);
    await post(h, withToken());
    expect(rows(h)[0].last_error).toBe("TypeError");
  });

  it.each(["CONTACT_TO", "CONTACT_FROM"] as const)("missing %s: 202 queued, not_configured, no send, alert", async (key) => {
    const h = createHarness({ [key]: undefined });
    let sendCalls = 0;
    h.env.CONTACT_EMAIL = {
      async send() {
        sendCalls += 1;
        return {};
      },
    };
    const { status, json } = await post(h, withToken());
    expect(status).toBe(202);
    expect(json.delivery).toBe("queued");
    expect(sendCalls).toBe(0);
    expect(rows(h)[0]).toMatchObject({ status: "failed", attempts: 1, last_error: "not_configured" });
    expect(webhookCalls(h)).toHaveLength(1);
  });

  it("an error while marking the row sent keeps the 202", async () => {
    const h = createHarness();
    h.db.failOn = /^UPDATE/;
    const { status, json } = await post(h, withToken());
    expect(status).toBe(202);
    expect(json.delivery).toBe("sent");
    expect(h.mails).toHaveLength(1);
  });
});

describe("POST /api/contact: store failure (A4)", () => {
  it("D1 insert throws: 503 store_failed, no send, alert attempted", async () => {
    const h = createHarness();
    h.db.failOn = /INSERT/;
    const { status, json } = await post(h, withToken());
    expect(status).toBe(503);
    expect(json).toEqual({ ok: false, error: "store_failed" });
    expect(h.mails).toHaveLength(0);
    expect(webhookCalls(h)).toHaveLength(1);
    expect(errors.some((e) => e.startsWith("contact_store_failed"))).toBe(true);
    for (const e of errors) expectNoPersonalData(e);
  });

  it("D1 table creation throws: also 503 store_failed", async () => {
    const h = createHarness();
    h.db.failOn = /CREATE TABLE/;
    const { status } = await post(h, withToken());
    expect(status).toBe(503);
    expect(h.mails).toHaveLength(0);
  });
});

describe("POST /api/contact: Turnstile (A5)", () => {
  it("control: a good token gives 202 and the siteverify body carries secret, token and remoteip", async () => {
    const h = createHarness();
    const { status } = await post(h, withToken(), { "CF-Connecting-IP": "203.0.113.7" });
    expect(status).toBe(202);
    const calls = siteverifyCalls(h);
    expect(calls).toHaveLength(1);
    expect(calls[0].init.method).toBe("POST");
    expect(new Headers(calls[0].init.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      secret: "1x0000000000000000000000000000000AA",
      response: "XXXX.DUMMY.TOKEN.XXXX",
      remoteip: "203.0.113.7",
    });
  });

  it("omits remoteip when CF-Connecting-IP is absent", async () => {
    const h = createHarness();
    await post(h, withToken());
    expect(JSON.parse(String(siteverifyCalls(h)[0].init.body))).toEqual({
      secret: "1x0000000000000000000000000000000AA",
      response: "XXXX.DUMMY.TOKEN.XXXX",
    });
  });

  it.each([
    ["missing", validBody],
    ["empty", withToken(validBody, "")],
    ["not a string", { ...validBody, turnstileToken: 42 }],
    ["too long", withToken(validBody, "x".repeat(2049))],
  ])("token %s: 403 turnstile_failed, no siteverify call, no row", async (_label, body) => {
    const h = createHarness();
    const { status, json } = await post(h, body);
    expect(status).toBe(403);
    expect(json).toEqual({ ok: false, error: "turnstile_failed" });
    expect(siteverifyCalls(h)).toHaveLength(0);
    expect(rows(h)).toHaveLength(0);
    expect(h.mails).toHaveLength(0);
  });

  it("control: a token of exactly 2048 characters is checked", async () => {
    const h = createHarness();
    const { status } = await post(h, withToken(validBody, "x".repeat(2048)));
    expect(status).toBe(202);
    expect(siteverifyCalls(h)).toHaveLength(1);
  });

  it("siteverify success:false: 403, no row, no mail", async () => {
    const h = createHarness();
    h.siteverify = { status: 200, body: { success: false, "error-codes": ["invalid-input-response"] } };
    const { status, json } = await post(h, withToken());
    expect(status).toBe(403);
    expect(json).toEqual({ ok: false, error: "turnstile_failed" });
    expect(rows(h)).toHaveLength(0);
    expect(h.mails).toHaveLength(0);
  });

  it.each([
    ["fetch throws", "throw" as const],
    ["non-2xx", { status: 500, body: { success: true } }],
    ["unreadable JSON", { status: 200, body: undefined }],
  ])("siteverify %s: 503 turnstile_unavailable, no row", async (_label, outcome) => {
    const h = createHarness();
    h.siteverify = outcome;
    const { status, json } = await post(h, withToken());
    expect(status).toBe(503);
    expect(json).toEqual({ ok: false, error: "turnstile_unavailable" });
    expect(rows(h)).toHaveLength(0);
    expect(h.mails).toHaveLength(0);
  });

  it.each([undefined, ""])("missing secret (%j): 503 turnstile_unavailable, no fetch", async (secret) => {
    const h = createHarness({ TURNSTILE_SECRET_KEY: secret });
    const { status, json } = await post(h, withToken());
    expect(status).toBe(503);
    expect(json).toEqual({ ok: false, error: "turnstile_unavailable" });
    expect(h.fetchCalls).toHaveLength(0);
    expect(rows(h)).toHaveLength(0);
  });
});

describe("POST /api/contact: check order (A6)", () => {
  it("content-length over 16384: 413 before anything else", async () => {
    const h = createHarness();
    const res = await handleContactRequest(
      contactRequest("not json", { "content-length": "16385" }),
      h.env,
      { fetch: h.fetch, now: h.now },
    );
    expect(res.status).toBe(413);
    expect(await res.json()).toEqual({ ok: false, error: "too_large" });
    expect(h.fetchCalls).toHaveLength(0);
  });

  it("a body over 16384 bytes without content-length: 413", async () => {
    const h = createHarness();
    const { status, json } = await post(h, withToken({ ...validBody, message: "a".repeat(17000) }));
    expect(status).toBe(413);
    expect(json).toEqual({ ok: false, error: "too_large" });
  });

  it("control: content-length at 16384 is read", async () => {
    const h = createHarness();
    const res = await handleContactRequest(
      contactRequest(withToken(), { "content-length": "16384" }),
      h.env,
      { fetch: h.fetch, now: h.now },
    );
    expect(res.status).toBe(202);
  });

  it("bad JSON: 400 invalid_json", async () => {
    const h = createHarness();
    const { status, json } = await post(h, "{not json");
    expect(status).toBe(400);
    expect(json).toEqual({ ok: false, error: "invalid_json" });
    expect(h.fetchCalls).toHaveLength(0);
  });

  it("invalid fields with a token: 400 invalid_fields, no siteverify call, no row", async () => {
    const h = createHarness();
    const { status, json } = await post(h, withToken({ ...validBody, email: "nope", company: "" }));
    expect(status).toBe(400);
    expect(json).toEqual({ ok: false, error: "invalid_fields", fields: ["email", "company"] });
    expect(siteverifyCalls(h)).toHaveLength(0);
    expect(rows(h)).toHaveLength(0);
  });
});

describe("POST /api/contact: fields (A7)", () => {
  it.each(["", "lt10k", "10k-50k", "50k-250k", "250k-1m", "gt1m"])("minutes %j is accepted", async (minutes) => {
    const h = createHarness();
    expect((await post(h, withToken({ ...validBody, minutes }))).status).toBe(202);
  });

  it("unknown minutes: 400 with minutes", async () => {
    const h = createHarness();
    const { status, json } = await post(h, withToken({ ...validBody, minutes: "lt5k" }));
    expect(status).toBe(400);
    expect(json.fields).toEqual(["minutes"]);
  });

  it.each(["+98 21 1234-5678", "(021) 1234 5678", "", "۰۹۱۲ ۳۴۵ ۶۷۸۹"])("phone %j is accepted", async (phone) => {
    const h = createHarness();
    expect((await post(h, withToken({ ...validBody, phone }))).status).toBe(202);
  });

  it.each(["abc", "+98 21 x", "1".repeat(33)])("phone %j: 400 with phone", async (phone) => {
    const h = createHarness();
    const { status, json } = await post(h, withToken({ ...validBody, phone }));
    expect(status).toBe(400);
    expect(json.fields).toEqual(["phone"]);
  });

  it("phone and minutes may be left out", async () => {
    const h = createHarness();
    const rest: Record<string, unknown> = { ...validBody };
    delete rest.phone;
    delete rest.minutes;
    expect((await post(h, withToken(rest))).status).toBe(202);
  });

  it("deployment is still required", async () => {
    const h = createHarness();
    const rest: Record<string, unknown> = { ...validBody };
    delete rest.deployment;
    const { status, json } = await post(h, withToken(rest));
    expect(status).toBe(400);
    expect(json.fields).toEqual(["deployment"]);
  });

  it("volume is no longer a field and is dropped from the payload", async () => {
    const h = createHarness();
    await post(h, withToken({ ...validBody, volume: "lt5k" }));
    expect(JSON.parse(String(rows(h)[0].payload))).not.toHaveProperty("volume");
  });
});

describe("POST /api/contact: alerts (A10)", () => {
  it("webhook unset: no webhook fetch, same response", async () => {
    const h = createHarness({ CONTACT_ALERT_WEBHOOK_URL: undefined });
    h.sendOutcome = new Error("boom");
    const { status, json } = await post(h, withToken());
    expect(status).toBe(202);
    expect(json.delivery).toBe("queued");
    expect(h.fetchCalls.map((c) => c.url)).toEqual(["https://challenges.cloudflare.com/turnstile/v0/siteverify"]);
    expect(errors.some((e) => e.startsWith("contact_delivery_failed"))).toBe(true);
  });

  it.each(["throw" as const, 500])("webhook %s: same response", async (outcome) => {
    const h = createHarness();
    h.sendOutcome = new Error("boom");
    h.webhook = outcome;
    const { status, json } = await post(h, withToken());
    expect(status).toBe(202);
    expect(json.delivery).toBe("queued");
  });

  it("webhook throws on a store failure: still 503 store_failed", async () => {
    const h = createHarness();
    h.db.failOn = /INSERT/;
    h.webhook = "throw";
    const { status, json } = await post(h, withToken());
    expect(status).toBe(503);
    expect(json.error).toBe("store_failed");
  });
});

describe("POST /api/contact: schema setup", () => {
  it("creates the table once per database, not once per request", async () => {
    const h = createHarness();
    await post(h, withToken());
    await post(h, withToken());
    expect(h.db.log.filter((sql) => sql.includes("CREATE TABLE"))).toHaveLength(1);
    expect(rows(h)).toHaveLength(2);
  });
});
