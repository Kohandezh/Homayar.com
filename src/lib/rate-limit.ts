import { NextResponse } from "next/server";

/**
 * Lightweight, dependency-free rate limiter for public API routes.
 *
 * It uses a per-instance in-memory fixed-window counter keyed by client IP +
 * bucket name. This is intentionally simple: it needs no external service and
 * stops the common abuse cases (form-spam floods, booking-slot grabbing, Stripe
 * session spam) on a single serverless/edge instance. For a horizontally-scaled
 * production deployment, swap the store for Upstash/Vercel KV — the `rateLimit`
 * signature stays the same.
 */
type Bucket = { count: number; resetAt: number };

const store = new Map<string, Bucket>();

// Periodically evict expired buckets so the map can't grow unbounded.
let lastSweep = 0;
function sweep(now: number) {
  if (now - lastSweep < 60_000) return;
  lastSweep = now;
  for (const [key, b] of store) {
    if (b.resetAt <= now) store.delete(key);
  }
}

/** Best-effort client IP from the standard proxy headers. */
export function clientIp(request: Request): string {
  const fwd = request.headers.get("x-forwarded-for");
  if (fwd) return fwd.split(",")[0].trim();
  return (
    request.headers.get("x-real-ip") ??
    request.headers.get("cf-connecting-ip") ??
    "unknown"
  );
}

export type RateLimitResult = {
  ok: boolean;
  remaining: number;
  retryAfterSec: number;
};

/**
 * @param key     unique caller identity (usually `${bucket}:${ip}`)
 * @param limit   max requests allowed within the window
 * @param windowMs window length in milliseconds
 */
export function rateLimit(
  key: string,
  limit: number,
  windowMs: number,
): RateLimitResult {
  const now = Date.now();
  sweep(now);

  const existing = store.get(key);
  if (!existing || existing.resetAt <= now) {
    store.set(key, { count: 1, resetAt: now + windowMs });
    return { ok: true, remaining: limit - 1, retryAfterSec: 0 };
  }

  if (existing.count >= limit) {
    return {
      ok: false,
      remaining: 0,
      retryAfterSec: Math.max(1, Math.ceil((existing.resetAt - now) / 1000)),
    };
  }

  existing.count += 1;
  return {
    ok: true,
    remaining: limit - existing.count,
    retryAfterSec: 0,
  };
}

/**
 * Convenience guard for route handlers. Returns a 429 `NextResponse` when the
 * caller is over the limit, or `null` when the request may proceed.
 */
export function enforceRateLimit(
  request: Request,
  bucket: string,
  limit: number,
  windowMs: number,
): NextResponse | null {
  const result = rateLimit(`${bucket}:${clientIp(request)}`, limit, windowMs);
  if (result.ok) return null;
  return NextResponse.json(
    { error: "Too many requests. Please slow down and try again shortly." },
    {
      status: 429,
      headers: { "Retry-After": String(result.retryAfterSec) },
    },
  );
}
