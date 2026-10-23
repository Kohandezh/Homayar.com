import type { ContactInput } from "../contact-schema";
import { errorCode } from "./alert";
import type { ContactEnv } from "./handle";
import { buildOwnerEmail } from "./mail";

export type DeliveryOutcome = { ok: true } | { ok: false; error: string };

/** Sends one request to the owner. Never throws: a failure comes back as a short code. */
export async function deliver(env: ContactEnv, id: string, receivedAt: string, input: ContactInput): Promise<DeliveryOutcome> {
  if (!env.CONTACT_TO || !env.CONTACT_FROM) return { ok: false, error: "not_configured" };
  const mail = buildOwnerEmail(id, receivedAt, input, { to: env.CONTACT_TO, from: env.CONTACT_FROM });
  try {
    await env.CONTACT_EMAIL.send(mail);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: errorCode(err) };
  }
}
