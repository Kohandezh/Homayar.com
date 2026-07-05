import { NextResponse } from "next/server";
import { z } from "zod";
import { getDb, hasDb } from "@/lib/db";
import { demoRequests, leads } from "@/db/schema";
import { enforceRateLimit } from "@/lib/rate-limit";

const demoSchema = z.object({
  name: z.string().min(1).max(200),
  email: z.string().email().max(320),
  company: z.string().max(200).optional().default(""),
  teamSize: z.string().max(50).optional().default(""),
  message: z.string().max(4000).optional().default(""),
});

/** Contact / book-a-demo form. Persists to Neon when configured. */
export async function POST(request: Request) {
  const limited = enforceRateLimit(request, "demo", 5, 60_000);
  if (limited) return limited;

  let data: z.infer<typeof demoSchema>;
  try {
    data = demoSchema.parse(await request.json());
  } catch {
    return NextResponse.json(
      { error: "Please provide a valid name and email." },
      { status: 400 },
    );
  }

  if (hasDb()) {
    try {
      const db = getDb();
      await db.insert(demoRequests).values({
        name: data.name,
        email: data.email,
        company: data.company || null,
        teamSize: data.teamSize || null,
        message: data.message || null,
      });
      await db.insert(leads).values({
        name: data.name,
        email: data.email,
        company: data.company || null,
        teamSize: data.teamSize || null,
        message: data.message || null,
        source: "demo",
      });
    } catch (err) {
      console.error("[demo] failed to persist demo request:", err);
      return NextResponse.json(
        { error: "Could not save your request. Please email info@ksf.ir." },
        { status: 500 },
      );
    }
  } else {
    console.log("[demo] request received (no DB configured):", data.email);
  }

  return NextResponse.json({ ok: true });
}
