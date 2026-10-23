import type { ContactDeps, ContactEnv } from "./handle";

export interface DeliveryProblem {
  kind: "contact_delivery_failed" | "contact_store_failed";
  /** The request id, when a row exists. */
  id?: string;
  attempts: number;
  /** A fixed code such as `send_failed`, never free error text. */
  error: string;
}

/**
 * Writes one log line and, when a webhook is configured, posts a short alert.
 * Carries only the id, the attempts count and the error code: no visitor data.
 * Never throws.
 */
export async function reportProblem(env: ContactEnv, deps: ContactDeps, problem: DeliveryProblem): Promise<void> {
  const { kind, id, attempts, error } = problem;
  console.error(`${kind} ${JSON.stringify({ id: id ?? null, attempts, error })}`);

  const url = env.CONTACT_ALERT_WEBHOOK_URL;
  if (!url) return;
  const what =
    kind === "contact_store_failed"
      ? "Homayar contact request delivery failed: the request could not be stored."
      : "Homayar contact request delivery failed.";
  const text = `${what} Request id: ${id ?? "none"}. Attempts: ${attempts}. Error: ${error}.`;
  try {
    const doFetch = deps.fetch ?? globalThis.fetch;
    const res = await doFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) console.warn(`contact_alert_failed ${JSON.stringify({ id: id ?? null, status: res.status })}`);
  } catch {
    console.warn(`contact_alert_failed ${JSON.stringify({ id: id ?? null, status: null })}`);
  }
}

/** A short code for a thrown error. Never the message, which can repeat an address. */
export function errorCode(err: unknown): string {
  if (err && typeof err === "object") {
    const code = (err as { code?: unknown }).code;
    if (typeof code === "string" && /^E_[A-Z0-9_]{1,60}$/.test(code)) return code;
    const name = (err as { name?: unknown }).name;
    if (typeof name === "string" && name !== "Error" && /^[A-Za-z]{1,60}$/.test(name)) return name;
  }
  return "send_failed";
}
