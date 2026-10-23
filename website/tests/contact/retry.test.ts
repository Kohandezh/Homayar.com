import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ensureContactTable } from "@/lib/contact/store";
import { retryContactDeliveries } from "@/lib/contact/retry";
import { NOW, createHarness, validBody, webhookCalls, type Harness } from "./helpers";

const minute = 60_000;
const day = 24 * 60 * minute;
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

interface Seed {
  id: string;
  createdAgo: number;
  status: "pending" | "sent" | "failed" | "dead";
  attempts?: number;
  lastAttemptAgo?: number | null;
}

async function seed(h: Harness, list: Seed[]) {
  await ensureContactTable(h.env.CONTACT_DB);
  const insert = h.db.sqlite.prepare(
    "INSERT INTO contact_requests (id, created_at, payload, status, attempts, last_attempt_at) VALUES (?, ?, ?, ?, ?, ?)",
  );
  for (const s of list) {
    insert.run(
      s.id,
      ago(s.createdAgo),
      JSON.stringify(validBody),
      s.status,
      s.attempts ?? 0,
      s.lastAttemptAgo == null ? null : ago(s.lastAttemptAgo),
    );
  }
}

function row(h: Harness, id: string) {
  const r = h.db.sqlite.prepare("SELECT * FROM contact_requests WHERE id = ?").get(id);
  return r ? { ...r } : undefined;
}

async function run(h: Harness) {
  return retryContactDeliveries(h.env, { fetch: h.fetch, now: h.now });
}

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe("retryContactDeliveries (A9)", () => {
  it("sends a failed row whose last attempt was 6 minutes ago", async () => {
    const h = createHarness();
    await seed(h, [{ id: "f6", createdAgo: 10 * minute, status: "failed", attempts: 1, lastAttemptAgo: 6 * minute }]);
    expect(await run(h)).toEqual({ attempted: 1, sent: 1, failed: 0, dead: 0, purged: 0 });
    expect(row(h, "f6")).toMatchObject({
      status: "sent",
      attempts: 2,
      sent_at: NOW.toISOString(),
      last_attempt_at: NOW.toISOString(),
    });
    expect(h.mails).toHaveLength(1);
    expect(h.mails[0].replyTo).toBe(validBody.email);
  });

  it("does not try the same row when the last attempt was 2 minutes ago", async () => {
    const h = createHarness();
    await seed(h, [{ id: "f2", createdAgo: 10 * minute, status: "failed", attempts: 1, lastAttemptAgo: 2 * minute }]);
    expect(await run(h)).toEqual({ attempted: 0, sent: 0, failed: 0, dead: 0, purged: 0 });
    expect(row(h, "f2")).toMatchObject({ status: "failed", attempts: 1 });
    expect(h.mails).toHaveLength(0);
  });

  it("tries a pending row 6 minutes old, not one 2 minutes old", async () => {
    const h = createHarness();
    await seed(h, [
      { id: "p6", createdAgo: 6 * minute, status: "pending" },
      { id: "p2", createdAgo: 2 * minute, status: "pending" },
    ]);
    expect(await run(h)).toEqual({ attempted: 1, sent: 1, failed: 0, dead: 0, purged: 0 });
    expect(row(h, "p6")).toMatchObject({ status: "sent", attempts: 1 });
    expect(row(h, "p2")).toMatchObject({ status: "pending", attempts: 0 });
  });

  it("never sends sent or dead rows", async () => {
    const h = createHarness();
    await seed(h, [
      { id: "s", createdAgo: day, status: "sent", attempts: 1, lastAttemptAgo: day },
      { id: "d", createdAgo: day, status: "dead", attempts: 5, lastAttemptAgo: day },
      { id: "f5", createdAgo: day, status: "failed", attempts: 5, lastAttemptAgo: day },
    ]);
    expect(await run(h)).toEqual({ attempted: 0, sent: 0, failed: 0, dead: 0, purged: 0 });
    expect(h.mails).toHaveLength(0);
  });

  it("a failed row with attempts 4 whose send fails becomes dead, with one alert", async () => {
    const h = createHarness();
    h.sendOutcome = new Error(`rejected ${validBody.email}`);
    await seed(h, [{ id: "f4", createdAgo: day, status: "failed", attempts: 4, lastAttemptAgo: 6 * minute }]);
    expect(await run(h)).toEqual({ attempted: 1, sent: 0, failed: 0, dead: 1, purged: 0 });
    expect(row(h, "f4")).toMatchObject({ status: "dead", attempts: 5, last_error: "send_failed", last_attempt_at: NOW.toISOString() });
    const alerts = webhookCalls(h);
    expect(alerts).toHaveLength(1);
    const text = JSON.parse(String(alerts[0].init.body)).text as string;
    expect(text).toContain("f4");
    for (const v of [validBody.name, validBody.email, validBody.phone, validBody.company, validBody.message]) {
      expect(text).not.toContain(v);
    }
  });

  it("a failed row with attempts 1 whose send fails becomes failed with attempts 2 and no alert", async () => {
    const h = createHarness();
    h.sendOutcome = new Error("boom");
    await seed(h, [{ id: "f1", createdAgo: day, status: "failed", attempts: 1, lastAttemptAgo: 6 * minute }]);
    expect(await run(h)).toEqual({ attempted: 1, sent: 0, failed: 1, dead: 0, purged: 0 });
    expect(row(h, "f1")).toMatchObject({ status: "failed", attempts: 2, last_error: "send_failed" });
    expect(webhookCalls(h)).toHaveLength(0);
  });

  it("a missing CONTACT_TO counts as a failed attempt, without a send", async () => {
    const h = createHarness({ CONTACT_TO: undefined });
    await seed(h, [{ id: "f1", createdAgo: day, status: "failed", attempts: 1, lastAttemptAgo: 6 * minute }]);
    expect(await run(h)).toEqual({ attempted: 1, sent: 0, failed: 1, dead: 0, purged: 0 });
    expect(row(h, "f1")).toMatchObject({ status: "failed", attempts: 2, last_error: "not_configured" });
  });

  it("purges rows older than 180 days and keeps a 179-day row", async () => {
    const h = createHarness();
    await seed(h, [
      { id: "old-sent", createdAgo: 181 * day, status: "sent", attempts: 1, lastAttemptAgo: 181 * day },
      { id: "old-dead", createdAgo: 200 * day, status: "dead", attempts: 5, lastAttemptAgo: 199 * day },
      { id: "keep", createdAgo: 179 * day, status: "sent", attempts: 1, lastAttemptAgo: 179 * day },
    ]);
    expect(await run(h)).toEqual({ attempted: 0, sent: 0, failed: 0, dead: 0, purged: 2 });
    expect(row(h, "old-sent")).toBeUndefined();
    expect(row(h, "old-dead")).toBeUndefined();
    expect(row(h, "keep")).toBeDefined();
  });

  it("tries at most 20 rows per run, oldest first", async () => {
    const h = createHarness();
    await seed(
      h,
      Array.from({ length: 25 }, (_, i) => ({
        id: `p${String(i).padStart(2, "0")}`,
        createdAgo: (100 - i) * minute,
        status: "pending" as const,
      })),
    );
    expect(await run(h)).toEqual({ attempted: 20, sent: 20, failed: 0, dead: 0, purged: 0 });
    expect(row(h, "p19")).toMatchObject({ status: "sent" });
    expect(row(h, "p20")).toMatchObject({ status: "pending" });
  });

  it("one row's failure does not stop the others", async () => {
    const h = createHarness();
    await seed(h, [
      { id: "a", createdAgo: 30 * minute, status: "pending" },
      { id: "b", createdAgo: 20 * minute, status: "pending" },
    ]);
    h.db.sqlite.prepare("UPDATE contact_requests SET payload = ? WHERE id = ?").run("{broken", "a");
    expect(await run(h)).toEqual({ attempted: 2, sent: 1, failed: 1, dead: 0, purged: 0 });
    expect(row(h, "a")).toMatchObject({ status: "failed", attempts: 1, last_error: "bad_payload" });
    expect(row(h, "b")).toMatchObject({ status: "sent", attempts: 1 });
  });

  it("works on an empty database (creates the table first)", async () => {
    const h = createHarness();
    expect(await run(h)).toEqual({ attempted: 0, sent: 0, failed: 0, dead: 0, purged: 0 });
  });
});
