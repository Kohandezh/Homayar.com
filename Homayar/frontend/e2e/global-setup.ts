import type { Server } from "node:net";
import { API_URL, SMTP_PORT } from "./demo";
import { startSmtpSink } from "./smtp-sink";

interface SinkHolder {
  __homayarSmtpSink?: Server;
}

/**
 * Null when the backend answers at all. A degraded dependency (`/readyz`
 * replies 503) still serves every flow these tests drive, so only a dead
 * socket counts as a problem.
 */
async function backendProblem(): Promise<string | null> {
  try {
    await fetch(`${API_URL}/readyz`, { signal: AbortSignal.timeout(5_000) });
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}

export default async function globalSetup() {
  const problem = await backendProblem();
  if (problem) {
    throw new Error(
      `The backend at ${API_URL} is not reachable (${problem}).\n` +
        "Start it with `make backend`, seed the demo workspace with `make demo-seed`,\n" +
        "and point E2E_API_URL at it if it listens elsewhere. See e2e/README.md.",
    );
  }

  let sink: Server;
  try {
    sink = await startSmtpSink(SMTP_PORT);
  } catch (error) {
    throw new Error(
      `Could not open the SMTP sink on 127.0.0.1:${SMTP_PORT} (${String(error)}).\n` +
        "Free the port or set E2E_SMTP_PORT, and start the backend with the same\n" +
        "SMTP_HOST and SMTP_PORT. See e2e/README.md.",
    );
  }
  (globalThis as SinkHolder).__homayarSmtpSink = sink;
}
