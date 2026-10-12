import type { Server } from "node:net";
import { removeMailbox } from "./smtp-sink";

interface SinkHolder {
  __homayarSmtpSink?: Server;
}

export default async function globalTeardown() {
  const sink = (globalThis as SinkHolder).__homayarSmtpSink;
  if (sink) await new Promise<void>((resolve) => sink.close(() => resolve()));
  // The captured mail carries a live one-time invitation token.
  removeMailbox();
}
