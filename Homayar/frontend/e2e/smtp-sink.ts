import { createServer, type Server } from "node:net";
import { appendFileSync, existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * A throwaway SMTP server for the tests.
 *
 * A workspace invitation only survives when its e-mail is delivered: without
 * SMTP the backend revokes the invitation it just created, writes a
 * `member.invitation.delivery_failed` audit event and answers 503
 * (`backend/routers/v1/members.py`). So the invite flow needs a mail server,
 * and the accept flow needs the one-time token that only the mail carries.
 *
 * This sink speaks just enough SMTP for Python's `smtplib`, and appends every
 * message it accepts to a JSON-lines file the tests read. It keeps no state
 * between runs: the global setup truncates the file first.
 */

const here = dirname(fileURLToPath(import.meta.url));

/** Where captured messages are written. Git-ignored. */
export const MAILBOX_FILE = `${here}/.mailbox.jsonl`;

export const SMTP_GREETING = "220 homayar-e2e SMTP sink\r\n";

export interface CapturedEmail {
  /** Recipients from `RCPT TO`. */
  to: string[];
  /** The message as sent, headers included. */
  raw: string;
  at: number;
}

function recipientOf(command: string): string {
  const angled = /<([^>]*)>/.exec(command);
  if (angled) return angled[1].trim().toLowerCase();
  return command.slice(command.indexOf(":") + 1).trim().toLowerCase();
}

/**
 * The protocol, with no socket in sight: feed it what the client sent and it
 * returns what to send back. `close` is true once the client said QUIT.
 */
export function createSmtpSession(onMessage: (mail: CapturedEmail) => void) {
  let recipients: string[] = [];
  let body = "";
  let buffer = "";
  let inData = false;

  return {
    receive(chunk: string): { reply: string; close: boolean } {
      buffer += chunk;
      let reply = "";
      for (;;) {
        if (inData) {
          const end = buffer.indexOf("\r\n.\r\n");
          if (end === -1) return { reply, close: false };
          body += buffer.slice(0, end);
          buffer = buffer.slice(end + 5);
          inData = false;
          onMessage({ to: recipients, raw: body, at: Date.now() });
          recipients = [];
          body = "";
          reply += "250 2.0.0 Ok: queued\r\n";
          continue;
        }

        const lineEnd = buffer.indexOf("\r\n");
        if (lineEnd === -1) return { reply, close: false };
        const command = buffer.slice(0, lineEnd);
        buffer = buffer.slice(lineEnd + 2);
        const verb = command.slice(0, 4).toUpperCase();

        // No STARTTLS and no AUTH are advertised, so smtplib sends neither.
        if (verb === "EHLO") reply += "250-homayar-e2e\r\n250 HELP\r\n";
        else if (verb === "RCPT") {
          recipients.push(recipientOf(command));
          reply += "250 2.1.5 Ok\r\n";
        } else if (verb === "DATA") {
          inData = true;
          body = "";
          reply += "354 End data with <CR><LF>.<CR><LF>\r\n";
        } else if (verb === "RSET") {
          recipients = [];
          reply += "250 2.0.0 Ok\r\n";
        } else if (verb === "QUIT") {
          return { reply: `${reply}221 2.0.0 Bye\r\n`, close: true };
        } else reply += "250 2.0.0 Ok\r\n";
      }
    },
  };
}

/** Start the sink and empty the mailbox. Rejects when the port is taken. */
export function startSmtpSink(port: number): Promise<Server> {
  writeFileSync(MAILBOX_FILE, "");
  const server = createServer((socket) => {
    const session = createSmtpSession((mail) =>
      appendFileSync(MAILBOX_FILE, `${JSON.stringify(mail)}\n`),
    );
    socket.setEncoding("utf8");
    socket.on("error", () => socket.destroy());
    socket.write(SMTP_GREETING);
    socket.on("data", (chunk: string) => {
      const { reply, close } = session.receive(chunk);
      if (reply) socket.write(reply);
      if (close) socket.end();
    });
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}

/**
 * Delete the mailbox file. The teardown calls it: a captured message holds a
 * live one-time invitation token, which has no reason to sit on disk after
 * the run.
 */
export function removeMailbox() {
  rmSync(MAILBOX_FILE, { force: true });
}

export function readMailbox(): CapturedEmail[] {
  if (!existsSync(MAILBOX_FILE)) return [];
  return readFileSync(MAILBOX_FILE, "utf8")
    .split("\n")
    .filter((line) => line.length > 0)
    .map((line) => JSON.parse(line) as CapturedEmail);
}

function decodeQuotedPrintable(text: string): string {
  const bytes: number[] = [];
  const joined = text.replace(/=\r?\n/g, ""); // soft line breaks
  for (let index = 0; index < joined.length; index += 1) {
    const hex = joined.slice(index + 1, index + 3);
    if (joined[index] === "=" && /^[0-9A-Fa-f]{2}$/.test(hex)) {
      bytes.push(parseInt(hex, 16));
      index += 2;
    } else bytes.push(...Buffer.from(joined[index], "utf8"));
  }
  return Buffer.from(bytes).toString("utf8");
}

/**
 * The message text. A Persian workspace name makes the body non-ASCII, so
 * Python encodes it base64 or quoted-printable; the header says which. Both
 * encodings turn a plain `=` into something else, so guessing is not safe.
 */
export function decodeBody(raw: string): string {
  const split = raw.indexOf("\r\n\r\n");
  const headers = split === -1 ? "" : raw.slice(0, split);
  const body = split === -1 ? raw : raw.slice(split + 4);
  const encoding = /^content-transfer-encoding:[ \t]*(\S+)/im.exec(headers)?.[1].toLowerCase();
  if (encoding === "base64") return Buffer.from(body.replace(/\s/g, ""), "base64").toString("utf8");
  if (encoding === "quoted-printable") return decodeQuotedPrintable(body);
  return body;
}

/** The one-time token from the newest invitation mail sent to `email`. */
export function tokenFromMail(mails: CapturedEmail[], email: string): string | null {
  const wanted = email.toLowerCase();
  for (const mail of mails.slice().reverse()) {
    if (!mail.to.includes(wanted)) continue;
    const match = /invitations\/accept\?token=([^\s"'&<>]+)/.exec(decodeBody(mail.raw));
    if (match) return decodeURIComponent(match[1]);
  }
  return null;
}

export const invitationTokenFor = (email: string) => tokenFromMail(readMailbox(), email);
