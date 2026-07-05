import { NextResponse } from "next/server";
import { z } from "zod";
import { getDb, hasDb } from "@/lib/db";
import { leads } from "@/db/schema";
import { enforceRateLimit } from "@/lib/rate-limit";

const contactSchema = z.object({
  firstName: z.string().min(1).max(200),
  lastName: z.string().max(200).optional().default(""),
  mobile: z.string().min(1).max(50),
  email: z.string().email().max(320).optional().or(z.literal("")),
  message: z.string().max(4000).optional().default(""),
  locale: z.enum(["fa", "en"]).optional().default("fa"),
  // Honeypot: a hidden field real users never fill. Bots that auto-fill every
  // input trip it, and we short-circuit with a fake success.
  company_url: z.string().max(200).optional().default(""),
});

/** Homayar landing contact form. Persists a lead to Neon when configured. */
export async function POST(request: Request) {
  const limited = enforceRateLimit(request, "contact", 5, 60_000);
  if (limited) return limited;

  let data: z.infer<typeof contactSchema>;
  try {
    data = contactSchema.parse(await request.json());
  } catch {
    return NextResponse.json(
      { error: "اطلاعات ارسالی نامعتبر است. / Invalid submission." },
      { status: 400 },
    );
  }

  // Honeypot tripped → pretend it worked, persist nothing.
  if (data.company_url) {
    return NextResponse.json({ ok: true });
  }

  const fullName = [data.firstName, data.lastName].filter(Boolean).join(" ").trim();
  // The phone number always belongs in the message so it survives even when no email is given.
  const note = [`tel: ${data.mobile}`, data.message].filter(Boolean).join(" — ");

  // Persist whenever a DB is configured — a missing email must never drop the
  // lead. The phone (the required field in fa locale) lives in `note`.
  if (hasDb()) {
    try {
      const db = getDb();
      await db.insert(leads).values({
        name: fullName,
        email: data.email || null,
        message: note,
        source: "homayar-contact",
      });
    } catch (err) {
      console.error("[contact] failed to persist lead:", err);
      return NextResponse.json(
        { error: "ثبت پیام با خطا مواجه شد. / Could not save your message." },
        { status: 500 },
      );
    }
  } else {
    console.log("[contact] lead received (no DB configured):", fullName, data.mobile);
  }

  return NextResponse.json({ ok: true });
}
