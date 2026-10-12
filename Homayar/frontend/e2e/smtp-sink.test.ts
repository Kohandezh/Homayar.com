// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createSmtpSession, tokenFromMail, type CapturedEmail } from "./smtp-sink";

/**
 * Unit tests for the SMTP sink the Playwright flows rely on. They run under
 * Vitest (`npm test`), because the protocol and the token extraction are the
 * parts most likely to break, and neither needs a browser or a socket. Hence
 * the `node` environment: the sink is Node code, jsdom would only hide that.
 */

const TOKEN = "Ab3-_x".repeat(8); // 48 characters, like a real invitation token
const ACCEPT_URL = `http://localhost:5173/app/invitations/accept?token=${TOKEN}`;

/** One message, the way `smtplib.send_message` sends it. */
function deliver(body: string, headers = "") {
  const captured: CapturedEmail[] = [];
  const session = createSmtpSession((mail) => captured.push(mail));
  const replies = [
    session.receive("EHLO homayar\r\n"),
    session.receive("MAIL FROM:<no-reply@homayar.test>\r\n"),
    session.receive("RCPT TO:<Admin@Demo.Example.com>\r\n"),
    session.receive("DATA\r\n"),
    session.receive(`To: admin@demo.example.com\r\nSubject: Invitation\r\n${headers}\r\n${body}\r\n.\r\n`),
    session.receive("QUIT\r\n"),
  ];
  return { captured, replies };
}

describe("the SMTP session", () => {
  it("accepts a message and reports the recipient in lower case", () => {
    const { captured, replies } = deliver(`Accept the invitation: ${ACCEPT_URL}`);

    expect(replies[0].reply).toContain("250-homayar-e2e");
    expect(replies[3].reply).toContain("354 ");
    expect(replies[4].reply).toContain("250 2.0.0 Ok: queued");
    expect(replies[5].close).toBe(true);
    expect(captured).toHaveLength(1);
    expect(captured[0].to).toEqual(["admin@demo.example.com"]);
    expect(captured[0].raw).toContain("Subject: Invitation");
  });

  it("reassembles a message that arrives in pieces", () => {
    const captured: CapturedEmail[] = [];
    const session = createSmtpSession((mail) => captured.push(mail));
    session.receive("EHLO homayar\r\nMAIL FROM:<a@b.test>\r\nRCPT TO:<c@d.test>\r\nDA");
    session.receive("TA\r\n");
    session.receive("Subject: x\r\n\r\nfir");
    session.receive("st line\r\n.\r");
    expect(captured).toHaveLength(0);
    session.receive("\n");

    expect(captured).toHaveLength(1);
    expect(captured[0].raw).toContain("first line");
  });

  it("keeps the recipients of two messages apart on one connection", () => {
    const captured: CapturedEmail[] = [];
    const session = createSmtpSession((mail) => captured.push(mail));
    for (const recipient of ["one@demo.test", "two@demo.test"]) {
      session.receive(`MAIL FROM:<a@b.test>\r\nRCPT TO:<${recipient}>\r\nDATA\r\n`);
      session.receive(`Subject: for ${recipient}\r\n\r\nbody\r\n.\r\n`);
    }

    expect(captured.map((mail) => mail.to)).toEqual([["one@demo.test"], ["two@demo.test"]]);
  });
});

describe("tokenFromMail", () => {
  function mail(raw: string, to = "admin@demo.example.com"): CapturedEmail {
    return { to: [to], raw, at: Date.now() };
  }

  it("reads the token from a plain body", () => {
    const body = `To: x\r\n\r\nAccept the invitation: ${ACCEPT_URL}\r\n`;
    expect(tokenFromMail([mail(body)], "admin@demo.example.com")).toBe(TOKEN);
  });

  it("reads the token from a base64 body", () => {
    // A Persian workspace name pushes Python to base64-encode the whole part.
    const text = `به فضای کاری شرکت نمونه همایار دعوت شده‌اید.\nAccept the invitation: ${ACCEPT_URL}\n`;
    const encoded = Buffer.from(text, "utf8").toString("base64").replace(/(.{76})/g, "$1\r\n");
    const raw = `Content-Transfer-Encoding: base64\r\n\r\n${encoded}`;
    expect(tokenFromMail([mail(raw)], "admin@demo.example.com")).toBe(TOKEN);
  });

  it("reads the token from a quoted-printable body with a soft line break", () => {
    const encoded = ACCEPT_URL.replace(/=/g, "=3D");
    const raw =
      "Content-Transfer-Encoding: quoted-printable\r\n\r\n" +
      `Accept: ${encoded.slice(0, 40)}=\r\n${encoded.slice(40)}\r\n`;
    expect(tokenFromMail([mail(raw)], "admin@demo.example.com")).toBe(TOKEN);
  });

  it("does not mistake a plain `token=Ab…` for a quoted-printable escape", () => {
    // "=Ab" is two hex digits; only the header decides that nothing is encoded.
    const raw = `Subject: Invitation\r\n\r\nAccept the invitation: ${ACCEPT_URL}\r\n`;
    expect(tokenFromMail([mail(raw)], "admin@demo.example.com")).toBe(TOKEN);
  });

  it("takes the newest mail, so a stale token is never reused", () => {
    const older = `\r\n\r\nAccept the invitation: ${ACCEPT_URL}`;
    const newer = `\r\n\r\nAccept the invitation: ${ACCEPT_URL.replace(TOKEN, "newer-token")}`;
    expect(tokenFromMail([mail(older), mail(newer)], "admin@demo.example.com")).toBe("newer-token");
  });

  it("ignores mail addressed to somebody else", () => {
    const raw = `\r\n\r\nAccept the invitation: ${ACCEPT_URL}`;
    expect(tokenFromMail([mail(raw, "viewer@demo.example.com")], "admin@demo.example.com")).toBeNull();
  });
});
