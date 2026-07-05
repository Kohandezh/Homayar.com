// Shared, dependency-free booking logic. Imported by BOTH the API routes
// (src/app/api/book/*) and the calendar UI (src/components/site/booking.tsx),
// so availability rules can never drift between client and server. Must stay
// pure — no DB, no Node-only APIs (Intl is fine in the browser and on the edge).

/** Business timezone that availability is defined in. */
export const BUSINESS_TZ = "Asia/Tehran";

/** Length of one consultation slot, in minutes. */
export const SLOT_MINUTES = 30;

/** How far ahead bookings are allowed (days from today). */
export const HORIZON_DAYS = 30;

/** Minimum notice before a slot, in minutes (can't book the very next minute). */
export const LEAD_MINUTES = 120;

/** Open weekdays, 0=Sun … 6=Sat. Monday–Friday. */
const OPEN_WEEKDAYS = new Set([1, 2, 3, 4, 5]);

/** Daily working windows in business-tz wall-clock minutes from midnight.
 *  09:00–12:00 and 13:00–17:00 (lunch 12:00–13:00 excluded). */
const WORK_WINDOWS: ReadonlyArray<readonly [number, number]> = [
  [9 * 60, 12 * 60],
  [13 * 60, 17 * 60],
];

export type Slot = {
  /** Absolute start instant, ISO-8601 UTC. */
  start: string;
  /** Absolute end instant, ISO-8601 UTC. */
  end: string;
};

/* ───────────────────────── timezone helpers ───────────────────────── */

/** Offset in ms between wall-clock time in `timeZone` and UTC, at `instant`. */
function tzOffsetMs(instant: Date, timeZone: string): number {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const map: Record<string, number> = {};
  for (const p of dtf.formatToParts(instant)) {
    if (p.type !== "literal") map[p.type] = Number(p.value);
  }
  const asUTC = Date.UTC(
    map.year,
    map.month - 1,
    map.day,
    map.hour % 24,
    map.minute,
    map.second,
  );
  return asUTC - instant.getTime();
}

/** Convert a wall-clock time in `timeZone` to the absolute UTC instant. */
function zonedWallTimeToUtc(
  y: number,
  mo: number,
  d: number,
  hh: number,
  mm: number,
  timeZone: string,
): Date {
  const guess = Date.UTC(y, mo - 1, d, hh, mm, 0);
  const offset = tzOffsetMs(new Date(guess), timeZone);
  let utc = guess - offset;
  // One refinement pass handles the DST fall-back/spring-forward edge.
  const offset2 = tzOffsetMs(new Date(utc), timeZone);
  if (offset2 !== offset) utc = guess - offset2;
  return new Date(utc);
}

/** "YYYY-MM-DD" for a given instant, in the given timezone. */
export function dateKeyInTz(instant: Date, timeZone: string): string {
  const dtf = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return dtf.format(instant); // en-CA formats as YYYY-MM-DD
}

/* ───────────────────────── availability ───────────────────────── */

/** Parse "YYYY-MM-DD" into numeric parts (no Date, so no tz ambiguity). */
function parseDateKey(dateKey: string): [number, number, number] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateKey);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

/** Weekday (0=Sun..6=Sat) of a calendar date — unambiguous via UTC midnight. */
export function weekdayOf(dateKey: string): number {
  const parts = parseDateKey(dateKey);
  if (!parts) return -1;
  const [y, mo, d] = parts;
  return new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
}

/** Is this calendar date bookable at all (open weekday, today..horizon)? */
export function isOpenDay(dateKey: string, now: Date = new Date()): boolean {
  const parts = parseDateKey(dateKey);
  if (!parts) return false;
  if (!OPEN_WEEKDAYS.has(weekdayOf(dateKey))) return false;

  const today = dateKeyInTz(now, BUSINESS_TZ);
  if (dateKey < today) return false;

  const horizon = new Date(now.getTime() + HORIZON_DAYS * 86_400_000);
  const horizonKey = dateKeyInTz(horizon, BUSINESS_TZ);
  return dateKey <= horizonKey;
}

/** All slots for a date that are still in the future (respecting lead time). */
export function slotsForDate(dateKey: string, now: Date = new Date()): Slot[] {
  const parts = parseDateKey(dateKey);
  if (!parts || !isOpenDay(dateKey, now)) return [];
  const [y, mo, d] = parts;
  const earliest = now.getTime() + LEAD_MINUTES * 60_000;

  const slots: Slot[] = [];
  for (const [from, to] of WORK_WINDOWS) {
    for (let min = from; min + SLOT_MINUTES <= to; min += SLOT_MINUTES) {
      const start = zonedWallTimeToUtc(
        y,
        mo,
        d,
        Math.floor(min / 60),
        min % 60,
        BUSINESS_TZ,
      );
      if (start.getTime() < earliest) continue;
      const end = new Date(start.getTime() + SLOT_MINUTES * 60_000);
      slots.push({ start: start.toISOString(), end: end.toISOString() });
    }
  }
  return slots;
}

/** True if `iso` is a valid, bookable slot start for its date. */
export function isValidSlot(iso: string, now: Date = new Date()): boolean {
  const start = new Date(iso);
  if (Number.isNaN(start.getTime())) return false;
  const dateKey = dateKeyInTz(start, BUSINESS_TZ);
  return slotsForDate(dateKey, now).some((s) => s.start === iso);
}

/* ───────────────────────── calendar export ───────────────────────── */

function icsStamp(d: Date): string {
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
}

function icsEscape(s: string): string {
  return s.replace(/([,;\\])/g, "\\$1").replace(/\n/g, "\\n");
}

export type CalendarEvent = {
  start: string; // ISO UTC
  end: string; // ISO UTC
  summary: string;
  description: string;
  location: string;
  uid: string;
};

/** A self-contained .ics file body the visitor can import into any calendar. */
export function buildICS(ev: CalendarEvent): string {
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Homayar//Booking//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "BEGIN:VEVENT",
    `UID:${ev.uid}`,
    `DTSTAMP:${icsStamp(new Date(ev.start))}`,
    `DTSTART:${icsStamp(new Date(ev.start))}`,
    `DTEND:${icsStamp(new Date(ev.end))}`,
    `SUMMARY:${icsEscape(ev.summary)}`,
    `DESCRIPTION:${icsEscape(ev.description)}`,
    `LOCATION:${icsEscape(ev.location)}`,
    "END:VEVENT",
    "END:VCALENDAR",
  ].join("\r\n");
}

/** "Add to Google Calendar" prefilled template URL. */
export function googleCalendarUrl(ev: CalendarEvent): string {
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: ev.summary,
    dates: `${icsStamp(new Date(ev.start))}/${icsStamp(new Date(ev.end))}`,
    details: ev.description,
    location: ev.location,
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}
