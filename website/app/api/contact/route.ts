import { contactSchema } from "@/lib/contact-schema";

/**
 * Demo request endpoint.
 * Validates the body with a schema and answers 202 { ok: true }.
 * It stores nothing, sends nothing and logs nothing personal. Delivery is wired in a later work unit.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: "invalid_json" }, { status: 400 });
  }

  const parsed = contactSchema.safeParse(body);
  if (!parsed.success) {
    const fields = [...new Set(parsed.error.issues.map((i) => String(i.path[0] ?? "body")))];
    return Response.json({ ok: false, error: "invalid_fields", fields }, { status: 400 });
  }

  return Response.json({ ok: true }, { status: 202 });
}
