import { contactSchema } from "@/lib/contact-schema";

const maxBodyBytes = 16 * 1024;

export async function POST(request: Request) {
  const length = Number(request.headers.get("content-length") ?? 0);
  if (length > maxBodyBytes) {
    return Response.json({ ok: false, error: "too_large" }, { status: 413 });
  }

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
