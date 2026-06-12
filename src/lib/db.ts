import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import * as schema from "@/db/schema";

/**
 * Lazy Neon client. The site is fully functional without DATABASE_URL —
 * the catalog layer (src/lib/catalog.ts) falls back to the static seed data
 * in src/data/ so local dev and preview deployments never block on a database.
 */
export function hasDb(): boolean {
  return Boolean(process.env.DATABASE_URL);
}

let _db: ReturnType<typeof drizzle<typeof schema>> | null = null;

export function getDb() {
  if (!hasDb()) {
    throw new Error(
      "DATABASE_URL is not set. See .env.example for required environment variables.",
    );
  }
  if (!_db) {
    const sql = neon(process.env.DATABASE_URL!);
    _db = drizzle(sql, { schema });
  }
  return _db;
}
