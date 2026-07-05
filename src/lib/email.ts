// Transactional email via Resend's HTTP API. Kept dependency-free (a plain
// fetch) and gracefully optional, mirroring src/lib/db.ts: when RESEND_API_KEY
// is absent the site still works — sends are skipped and logged. A failed send
// never throws to the caller, so it can't roll back an already-saved booking.

type Lang = "fa" | "en";

export function hasEmail(): boolean {
  return Boolean(process.env.RESEND_API_KEY);
}

/** Verified Resend sender, e.g. "Homayar <booking@homayar.com>". */
function fromAddress(): string {
  return process.env.EMAIL_FROM || "Homayar <onboarding@resend.dev>";
}

type Attachment = { filename: string; content: string };

async function send(opts: {
  to: string | string[];
  subject: string;
  html: string;
  attachments?: Attachment[];
}): Promise<void> {
  if (!hasEmail()) {
    console.log("[email] skipped (no RESEND_API_KEY):", opts.subject, "->", opts.to);
    return;
  }
  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ from: fromAddress(), ...opts }),
    });
    if (!res.ok) {
      console.error("[email] send failed:", res.status, await res.text());
    }
  } catch (err) {
    console.error("[email] send threw:", err);
  }
}

const COPY = {
  fa: {
    dir: "rtl",
    subject: "تأیید رزرو جلسه همایار",
    greeting: (name: string) => `سلام ${name}،`,
    body: "جلسه مشاوره شما با تیم همایار ثبت شد. جزئیات زیر است:",
    when: "زمان",
    note: "لینک جلسه پیش از موعد برایتان ارسال می‌شود. برای افزودن به تقویم، فایل پیوست را باز کنید.",
    sign: "تیم همایار",
  },
  en: {
    dir: "ltr",
    subject: "Your Homayar consultation is confirmed",
    greeting: (name: string) => `Hi ${name},`,
    body: "Your consultation with the Homayar team is booked. Here are the details:",
    when: "When",
    note: "We'll email the meeting link before your session. Open the attached file to add it to your calendar.",
    sign: "The Homayar team",
  },
} as const;

function formatWhen(startISO: string, lang: Lang): string {
  // Shown in Tehran time so the confirmation matches what was booked on-site.
  return new Date(startISO).toLocaleString(lang === "fa" ? "fa-IR-u-ca-gregory" : "en-US", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Tehran",
    timeZoneName: "short",
  });
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Confirmation to the visitor (with .ics) plus optional team notification. */
export async function sendBookingConfirmation(opts: {
  to: string;
  name: string;
  company?: string;
  startISO: string;
  ics: string;
  lang?: Lang;
}): Promise<void> {
  const lang: Lang = opts.lang === "en" ? "en" : "fa";
  const c = COPY[lang];
  const when = formatWhen(opts.startISO, lang);
  const name = escapeHtml(opts.name);

  const html = `<!doctype html><html dir="${c.dir}"><body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0a0b10;color:#e9edf2;padding:32px">
    <div style="max-width:520px;margin:0 auto;background:#13151c;border:1px solid #262a34;border-radius:16px;padding:28px">
      <h1 style="margin:0 0 8px;font-size:20px;color:#fff">${escapeHtml(c.subject)}</h1>
      <p style="color:#b9c1cc;margin:0 0 16px">${escapeHtml(c.greeting(opts.name)).replace(name, name)}</p>
      <p style="color:#b9c1cc;margin:0 0 16px">${escapeHtml(c.body)}</p>
      <div style="background:#0a0b10;border:1px solid #262a34;border-radius:12px;padding:16px;margin:0 0 16px">
        <div style="color:#7c8696;font-size:12px;text-transform:uppercase;letter-spacing:.08em">${escapeHtml(c.when)}</div>
        <div style="color:#34d3c0;font-weight:700;font-size:16px;margin-top:4px">${escapeHtml(when)}</div>
      </div>
      <p style="color:#7c8696;font-size:13px;margin:0">${escapeHtml(c.note)}</p>
      <p style="color:#b9c1cc;margin:20px 0 0">${escapeHtml(c.sign)}</p>
    </div>
  </body></html>`;

  const attachments: Attachment[] = [
    {
      filename: "homayar-consultation.ics",
      content: Buffer.from(opts.ics, "utf-8").toString("base64"),
    },
  ];

  await send({ to: opts.to, subject: c.subject, html, attachments });

  const notify = process.env.BOOKING_NOTIFY_EMAIL;
  if (notify) {
    await send({
      to: notify,
      subject: `New booking: ${opts.name} — ${when}`,
      html: `<p>New consultation booked.</p>
        <ul>
          <li><b>Name:</b> ${name}</li>
          <li><b>Email:</b> ${escapeHtml(opts.to)}</li>
          ${opts.company ? `<li><b>Company:</b> ${escapeHtml(opts.company)}</li>` : ""}
          <li><b>When:</b> ${escapeHtml(when)}</li>
        </ul>`,
    });
  }
}
