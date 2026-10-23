import type { ContactInput } from "../contact-schema";

export const senderName = "Homayar website";

export interface OwnerEmail {
  to: string;
  from: { email: string; name: string };
  replyTo: string;
  subject: string;
  text: string;
  html: string;
}

const roleLabels: Record<string, string> = {
  qa: "QA manager",
  supervisor: "Supervisor or team lead",
  operations: "Contact-center or CX operations",
  it: "IT or security",
  other: "Other",
};

const deploymentLabels: Record<string, string> = {
  saas: "SaaS is fine",
  "self-hosted": "Self-hosted on their servers",
  hybrid: "Hybrid",
  unsure: "Not sure yet",
};

const minutesLabels: Record<string, string> = {
  lt10k: "Under 10,000 minutes",
  "10k-50k": "10,000 to 50,000 minutes",
  "50k-250k": "50,000 to 250,000 minutes",
  "250k-1m": "250,000 to 1,000,000 minutes",
  gt1m: "Over 1,000,000 minutes",
};

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function label(labels: Record<string, string>, value: string | undefined): string {
  if (!value) return "-";
  return labels[value] ?? value;
}

/** Builds the e-mail to the owner from a validated request. */
export function buildOwnerEmail(
  id: string,
  receivedAt: string,
  input: ContactInput,
  config: { to: string; from: string },
): OwnerEmail {
  const fields: [string, string][] = [
    ["Reference", id],
    ["Received", receivedAt],
    ["Name", input.name],
    ["E-mail", input.email],
    ["Company", input.company],
    ["Phone", input.phone || "-"],
    ["Role", label(roleLabels, input.role)],
    ["Deployment preference", label(deploymentLabels, input.deployment)],
    ["Monthly call minutes", label(minutesLabels, input.minutes)],
    ["Locale", input.locale || "-"],
  ];
  const message = input.message || "-";

  const text = [
    "New pilot request from homayar.com",
    "",
    ...fields.map(([k, v]) => `${k}: ${v}`),
    "",
    "Message:",
    message,
    "",
    "Reply to this e-mail to answer the visitor.",
  ].join("\n");

  const rowsHtml = fields
    .map(([k, v]) => `<tr><th align="left" style="padding:4px 12px 4px 0">${escapeHtml(k)}</th><td style="padding:4px 0">${escapeHtml(v)}</td></tr>`)
    .join("");
  const html = [
    "<!doctype html><html><body>",
    "<h1 style=\"font-size:18px\">New pilot request from homayar.com</h1>",
    `<table>${rowsHtml}</table>`,
    "<h2 style=\"font-size:16px\">Message</h2>",
    `<p style="white-space:pre-wrap">${escapeHtml(message)}</p>`,
    "<p>Reply to this e-mail to answer the visitor.</p>",
    "</body></html>",
  ].join("");

  return {
    to: config.to,
    from: { email: config.from, name: senderName },
    replyTo: input.email,
    // A header value must never hold a line break.
    subject: `Homayar pilot request: ${input.company}`.replace(/[\r\n]/g, ""),
    text,
    html,
  };
}
