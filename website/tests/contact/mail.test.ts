import { describe, expect, it } from "vitest";
import { buildOwnerEmail, escapeHtml } from "@/lib/contact/mail";
import { validBody } from "./helpers";

const config = { to: "owner@example.com", from: "website@homayar.com" };
const receivedAt = "2026-10-23T12:00:00.000Z";

describe("owner e-mail (A8)", () => {
  it("addresses the owner and replies to the visitor", () => {
    const mail = buildOwnerEmail("req-1", receivedAt, validBody, config);
    expect(mail.to).toBe("owner@example.com");
    expect(mail.from).toEqual({ email: "website@homayar.com", name: "Homayar website" });
    expect(mail.replyTo).toBe(validBody.email);
    expect(mail.subject).toBe("Homayar pilot request: Example Telecom");
  });

  it("removes every CR and LF from the subject", () => {
    const mail = buildOwnerEmail("req-1", receivedAt, { ...validBody, company: "Evil\r\nBcc: x@example.com\nCo\r" }, config);
    expect(mail.subject).not.toMatch(/[\r\n]/);
    expect(mail.subject).toBe("Homayar pilot request: EvilBcc: x@example.comCo");
  });

  it("escapes every visitor value in html", () => {
    const mail = buildOwnerEmail(
      "req-1",
      receivedAt,
      { ...validBody, name: `A & "B" 'C'`, message: "<script>alert(1)</script>" },
      config,
    );
    expect(mail.html).not.toContain("<script>");
    expect(mail.html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(mail.html).toContain("A &amp; &quot;B&quot; &#39;C&#39;");
  });

  it("text and html carry every field", () => {
    const mail = buildOwnerEmail("req-1", receivedAt, validBody, config);
    for (const body of [mail.text, mail.html]) {
      expect(body).toContain("req-1");
      expect(body).toContain(receivedAt);
      expect(body).toContain(validBody.name);
      expect(body).toContain(validBody.email);
      expect(body).toContain(validBody.company);
      expect(body).toContain("+98 21 1234-5678");
      expect(body).toContain("QA manager");
      expect(body).toContain("Self-hosted");
      expect(body).toContain("50,000 to 250,000");
      expect(body).toContain("en");
      expect(body).toContain("We run 40 agents on Issabel.");
    }
  });

  it("shows a dash for empty optional fields", () => {
    const mail = buildOwnerEmail(
      "req-1",
      receivedAt,
      { name: "Ali", email: "ali@example.org", company: "X", deployment: "saas" },
      config,
    );
    expect(mail.text).toContain("Phone: -");
    expect(mail.text).toContain("Monthly call minutes: -");
  });

  it("escapeHtml covers & < > \" '", () => {
    expect(escapeHtml(`&<>"'`)).toBe("&amp;&lt;&gt;&quot;&#39;");
  });
});
