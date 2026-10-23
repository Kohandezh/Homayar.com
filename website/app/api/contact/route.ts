import { getCloudflareContext } from "@opennextjs/cloudflare";
import { handleContactRequest, type ContactEnv } from "@/lib/contact/handle";

// The logic lives in lib/contact/handle.ts so it can be tested in plain Node.
// The bindings (D1, send_email) and secrets come from the Worker env.
export async function POST(request: Request) {
  const { env } = getCloudflareContext();
  return handleContactRequest(request, env as unknown as ContactEnv);
}
