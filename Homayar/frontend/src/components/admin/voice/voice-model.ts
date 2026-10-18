/** From this percent of the limit a capacity meter turns to warning (as in QuotaCard). */
export const NEAR_LIMIT_PERCENT = 80;

/** Share of the limit in percent, capped at 100. A zero limit counts as full. */
export function capacityPercent(held: number, limit: number): number {
  if (limit <= 0) return 100;
  return Math.min(100, Math.max(0, (held / limit) * 100));
}

export function isNearLimit(percent: number): boolean {
  return percent >= NEAR_LIMIT_PERCENT;
}

export interface HeartbeatAge {
  unit: "s" | "min" | "h";
  /** A whole number, rounded down. */
  value: number;
}

/**
 * How long ago a node sent its heartbeat, in the largest unit that fits:
 * seconds under a minute, minutes under an hour, then hours. A time in the
 * future (clock skew) counts as 0 s. Null when the time cannot be read.
 */
export function heartbeatAge(heartbeatAt: string, now: number): HeartbeatAge | null {
  const at = Date.parse(heartbeatAt);
  if (Number.isNaN(at)) return null;
  const seconds = Math.max(0, Math.floor((now - at) / 1000));
  if (seconds < 60) return { unit: "s", value: seconds };
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return { unit: "min", value: minutes };
  return { unit: "h", value: Math.floor(minutes / 60) };
}
