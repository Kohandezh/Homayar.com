import { NextResponse } from "next/server";
import { and, gte, lt, eq } from "drizzle-orm";
import { getDb, hasDb } from "@/lib/db";
import { bookings } from "@/db/schema";
import { slotsForDate } from "@/lib/booking";

/**
 * Available slots for a single calendar date ("YYYY-MM-DD").
 * Returns business-hours slots that are still in the future, minus any that are
 * already booked. Without a database every generated slot is offered (local dev
 * and previews stay functional, just non-persistent).
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const date = url.searchParams.get("date") ?? "";

  const slots = slotsForDate(date);
  if (slots.length === 0) {
    return NextResponse.json({ slots: [] });
  }

  if (!hasDb()) {
    return NextResponse.json({ slots });
  }

  try {
    const db = getDb();
    // Booked instants within this date's slot range.
    const first = new Date(slots[0].start);
    const last = new Date(slots[slots.length - 1].start);
    const dayAfter = new Date(last.getTime() + 60_000);
    const taken = await db
      .select({ startsAt: bookings.startsAt })
      .from(bookings)
      .where(
        and(
          gte(bookings.startsAt, first),
          lt(bookings.startsAt, dayAfter),
          eq(bookings.status, "confirmed"),
        ),
      );
    const takenSet = new Set(taken.map((r) => new Date(r.startsAt).toISOString()));
    return NextResponse.json({
      slots: slots.filter((s) => !takenSet.has(s.start)),
    });
  } catch (err) {
    console.error("[book/slots] availability lookup failed:", err);
    // Fail open with generated slots rather than blocking the user entirely.
    return NextResponse.json({ slots });
  }
}
