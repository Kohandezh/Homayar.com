import type { D1Database } from "./d1";

/** A row with this many failed attempts becomes dead. */
export const maxAttempts = 5;

export type ContactStatus = "pending" | "sent" | "failed" | "dead";

export interface ContactRow {
  id: string;
  created_at: string;
  payload: string;
  status: ContactStatus;
  attempts: number;
}

const createTable = `CREATE TABLE IF NOT EXISTS contact_requests (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_attempt_at TEXT,
  sent_at TEXT
)`;

// The table is created once per database binding in this isolate. A failed attempt
// is not remembered, so the next request tries again.
const ready = new WeakSet<D1Database>();

export async function ensureContactTable(db: D1Database): Promise<void> {
  if (ready.has(db)) return;
  await db.prepare(createTable).run();
  ready.add(db);
}

export async function insertPending(db: D1Database, id: string, createdAt: string, payload: string): Promise<void> {
  await ensureContactTable(db);
  await db
    .prepare("INSERT INTO contact_requests (id, created_at, payload, status, attempts) VALUES (?, ?, ?, ?, ?)")
    .bind(id, createdAt, payload, "pending", 0)
    .run();
}

export async function markSent(db: D1Database, id: string, attempts: number, at: string): Promise<void> {
  await db
    .prepare("UPDATE contact_requests SET status = ?, attempts = ?, last_attempt_at = ?, sent_at = ?, last_error = NULL WHERE id = ?")
    .bind("sent", attempts, at, at, id)
    .run();
}

export async function markFailed(
  db: D1Database,
  id: string,
  status: "failed" | "dead",
  attempts: number,
  at: string,
  error: string,
): Promise<void> {
  await db
    .prepare("UPDATE contact_requests SET status = ?, attempts = ?, last_attempt_at = ?, last_error = ? WHERE id = ?")
    .bind(status, attempts, at, error.slice(0, 200), id)
    .run();
}

export async function selectRetryable(db: D1Database, cutoff: string, limit: number): Promise<ContactRow[]> {
  const { results } = await db
    .prepare(
      `SELECT id, created_at, payload, status, attempts FROM contact_requests
       WHERE (status = 'failed' AND attempts < ? AND last_attempt_at <= ?)
          OR (status = 'pending' AND created_at <= ?)
       ORDER BY created_at ASC
       LIMIT ?`,
    )
    .bind(maxAttempts, cutoff, cutoff, limit)
    .all<ContactRow>();
  return results;
}

/** Deletes every row created before `cutoff` and returns how many there were. */
export async function purgeOlderThan(db: D1Database, cutoff: string): Promise<number> {
  const row = await db
    .prepare("SELECT COUNT(*) AS n FROM contact_requests WHERE created_at < ?")
    .bind(cutoff)
    .first<{ n: number }>();
  const count = Number(row?.n ?? 0);
  if (count > 0) {
    await db.prepare("DELETE FROM contact_requests WHERE created_at < ?").bind(cutoff).run();
  }
  return count;
}

