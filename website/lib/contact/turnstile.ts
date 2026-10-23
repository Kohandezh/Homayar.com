export const siteverifyUrl = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

/** Cloudflare documents 2048 characters as the maximum token length. */
export const maxTokenLength = 2048;

export type TurnstileResult = "passed" | "failed" | "unavailable";

/**
 * Checks a Turnstile token with siteverify.
 * https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
 */
export async function verifyTurnstile(
  token: unknown,
  secret: string | undefined,
  remoteIp: string | null,
  doFetch: typeof fetch,
): Promise<TurnstileResult> {
  // Fail closed: without a secret no request can pass.
  if (!secret) return "unavailable";
  if (typeof token !== "string" || token.length === 0 || token.length > maxTokenLength) return "failed";

  let res: Response;
  try {
    res = await doFetch(siteverifyUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(remoteIp ? { secret, response: token, remoteip: remoteIp } : { secret, response: token }),
    });
  } catch {
    return "unavailable";
  }
  if (!res.ok) return "unavailable";

  let outcome: unknown;
  try {
    outcome = await res.json();
  } catch {
    return "unavailable";
  }
  return (outcome as { success?: unknown } | null)?.success === true ? "passed" : "failed";
}
