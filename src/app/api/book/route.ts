import { NextResponse } from "next/server";
import { z } from "zod";
import { getDb, hasDb } from "@/lib/db";
import { bookings, leads } from "@/db/schema";
import {
  isValidSlot,
  SLOT_MINUTES,
  buildICS,
  googleCalendarUrl,
  type CalendarEvent,
} from "@/lib/booking";
import { sendBookingConfirmation } from "@/lib/email";
import { enforceRateLimit } from "@/lib/rate-limit";

const bookingSchema = z.object({
  slot: z.string().min(1),
  name: z.string().min(1).max(200),
  email: z.string().email().max(320),
  company: z.string().max(200).optional().default(""),
  message: z.string().max(4000).optional().default(""),
  timezone: z.string().max(64).optional().default(""),
  lang: z.enum(["fa", "en"]).optional().default("fa"),
});

function isUniqueViolation(err: unknown): boolean {
  // Postgres unique_violation code, surfaced by the Neon driver.
  return Boolean(
    err &&
      typeof err === "object" &&
      "code" in err &&
      (err as { code?: string }).code === "23505",
  );
}

/** Create a consultation booking for a specific slot. */
export async function POST(request: Request) {
  // Tighter limit: booking creates a durable row and grabs a unique slot, so
  // it's the most abuse-sensitive endpoint.
  const limited = enforceRateLimit(request, "book", 4, 60_000);
  if (limited) return limited;

  let data: z.infer<typeof bookingSchema>;
  try {
    data = bookingSchema.parse(await request.json());
  } catch {
    return NextResponse.json(
      { error: "Please provide your name, a valid email, and a time slot." },
      { status: 400 },
    );
  }

  // Re-validate the slot server-side: it must be a real, future business slot.
  if (!isValidSlot(data.slot)) {
    return NextResponse.json(
      { error: "That time is no longer available. Please pick another slot." },
      { status: 409 },
    );
  }

  const start = new Date(data.slot);
  const end = new Date(start.getTime() + SLOT_MINUTES * 60_000);

  if (hasDb()) {
    try {
      const db = getDb();
      await db.insert(bookings).values({
        name: data.name,
        email: data.email,
        company: data.company || null,
        message: data.message || null,
        startsAt: start,
        endsAt: end,
        timezone: data.timezone || null,
        status: "confirmed",
      });
      // Mirror into the shared leads table so all enquiries live in one place.
      await db.insert(leads).values({
        name: data.name,
        email: data.email,
        company: data.company || null,
        message: data.message || null,
        source: "booking",
      });
    } catch (err) {
      if (isUniqueViolation(err)) {
        return NextResponse.json(
          { error: "Someone just took that time. Please pick another slot." },
          { status: 409 },
        );
      }
      console.error("[book] failed to persist booking:", err);
      return NextResponse.json(
        { error: "Could not save your booking. Please email info@ksf.ir." },
        { status: 500 },
      );
    }
  } else {
    console.log("[book] booking received (no DB configured):", data.email, data.slot);
  }

  const ev: CalendarEvent = {
    start: start.toISOString(),
    end: end.toISOString(),
    summary: "Homayar consultation",
    description: `Consultation with the Homayar team.${
      data.company ? ` Company: ${data.company}.` : ""
    }`,
    location: "Online (link to follow by email)",
    uid: `booking-${start.getTime()}@homayar.com`,
  };

  const ics = buildICS(ev);

  // Confirmation email — best-effort, never blocks or fails the booking.
  await sendBookingConfirmation({
    to: data.email,
    name: data.name,
    company: data.company,
    startISO: ev.start,
    ics,
    lang: data.lang,
  });

  return NextResponse.json({
    ok: true,
    booking: { start: ev.start, end: ev.end },
    ics,
    googleUrl: googleCalendarUrl(ev),
  });
}
