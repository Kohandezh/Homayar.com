// The part of the Cloudflare D1 binding that the contact code uses. Declared here so
// the contact modules load in plain Node (tests) without the Workers runtime types.
// The real binding has more methods; the contact code must use only these.
export interface D1PreparedStatement {
  bind(...values: (string | number | null)[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<{ results: T[] }>;
  run(): Promise<unknown>;
}

export interface D1Database {
  prepare(sql: string): D1PreparedStatement;
}
