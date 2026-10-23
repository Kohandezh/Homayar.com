import { contactSchema, type ContactInput } from "../contact-schema";
import { reportProblem } from "./alert";
import { deliver, type DeliveryOutcome } from "./deliver";
import type { ContactDeps, ContactEnv } from "./handle";
import { ensureContactTable, markFailed, markSent, maxAttempts, purgeOlderThan, selectRetryable, type ContactRow } from "./store";

const retryDelayMs = 5 * 60 * 1000;
const retentionMs = 180 * 24 * 60 * 60 * 1000;
const batchSize = 20;

export interface RetryCounts {
  attempted: number;
  sent: number;
  failed: number;
  dead: number;
  purged: number;
}

function parsePayload(row: ContactRow): ContactInput | null {
  try {
    const parsed = contactSchema.safeParse(JSON.parse(row.payload));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

/**
 * Runs from the cron trigger (worker.ts `scheduled`). Sends again every request that
 * failed or stopped half-way, then deletes requests older than 180 days.
 */
export async function retryContactDeliveries(env: ContactEnv, deps: ContactDeps = {}): Promise<RetryCounts> {
  const now = deps.now ?? (() => new Date());
  const start = now().getTime();
  const counts: RetryCounts = { attempted: 0, sent: 0, failed: 0, dead: 0, purged: 0 };
  const db = env.CONTACT_DB;

  await ensureContactTable(db);
  const rows = await selectRetryable(db, new Date(start - retryDelayMs).toISOString(), batchSize);

  for (const row of rows) {
    counts.attempted += 1;
    const attempts = Number(row.attempts) + 1;
    try {
      const input = parsePayload(row);
      const outcome: DeliveryOutcome = input
        ? await deliver(env, row.id, row.created_at, input)
        : { ok: false, error: "bad_payload" };
      const at = now().toISOString();

      if (outcome.ok) {
        counts.sent += 1;
        await markSent(db, row.id, attempts, at);
        continue;
      }
      if (attempts >= maxAttempts) {
        counts.dead += 1;
        await markFailed(db, row.id, "dead", attempts, at, outcome.error);
        await reportProblem(env, deps, { kind: "contact_delivery_failed", id: row.id, attempts, error: outcome.error });
      } else {
        counts.failed += 1;
        await markFailed(db, row.id, "failed", attempts, at, outcome.error);
        console.warn(`contact_retry_failed ${JSON.stringify({ id: row.id, attempts, error: outcome.error })}`);
      }
    } catch {
      // A database error on one row must not stop the others.
      console.warn(`contact_retry_row_failed ${JSON.stringify({ id: row.id })}`);
    }
  }

  counts.purged = await purgeOlderThan(db, new Date(start - retentionMs).toISOString());
  return counts;
}
