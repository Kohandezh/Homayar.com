import { DatabaseSync, type SQLInputValue } from "node:sqlite";

// A D1 stand-in backed by an in-memory SQLite database. It implements only the
// surface the contact code may use: prepare, bind, first, all, run.
export interface FakeD1 {
  prepare(sql: string): FakeStatement;
  /** Direct access for test setup and assertions. Not part of the D1 surface. */
  sqlite: DatabaseSync;
  /** Every SQL text passed to prepare, in order. */
  log: string[];
  /** Make every statement whose SQL matches this pattern throw. */
  failOn: RegExp | null;
}

interface FakeStatement {
  bind(...values: unknown[]): FakeStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<{ results: T[] }>;
  run(): Promise<{ success: true }>;
}

export function createFakeD1(): FakeD1 {
  const sqlite = new DatabaseSync(":memory:");
  const db: FakeD1 = {
    sqlite,
    log: [],
    failOn: null,
    prepare(sql: string) {
      db.log.push(sql);
      return statement(sql, []);
    },
  };

  function check(sql: string) {
    if (db.failOn && db.failOn.test(sql)) throw new Error("D1_ERROR: fake failure");
  }

  function statement(sql: string, values: SQLInputValue[]): FakeStatement {
    return {
      bind(...next: unknown[]) {
        for (const v of next) {
          if (v !== null && typeof v !== "string" && typeof v !== "number") {
            throw new TypeError(`fake D1: bind only strings, numbers and null, got ${typeof v}`);
          }
        }
        return statement(sql, next as SQLInputValue[]);
      },
      async first<T>() {
        check(sql);
        const row = sqlite.prepare(sql).get(...values);
        return (row ? { ...row } : null) as T | null;
      },
      async all<T>() {
        check(sql);
        const rows = sqlite.prepare(sql).all(...values);
        return { results: rows.map((r) => ({ ...r })) as T[] };
      },
      async run() {
        check(sql);
        sqlite.prepare(sql).run(...values);
        return { success: true as const };
      },
    };
  }

  return db;
}
