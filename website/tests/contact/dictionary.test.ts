import { describe, expect, it } from "vitest";
import en from "@/i18n/en";
import fa from "@/i18n/fa";

// The shape of a value: object keys and array lengths, not the text.
function shape(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(shape);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, shape((value as Record<string, unknown>)[k])]));
  }
  return typeof value;
}

function strings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (value && typeof value === "object") return Object.values(value).flatMap(strings);
  return [];
}

describe("contact copy (A11)", () => {
  it("both dictionaries have the same key shape", () => {
    expect(shape(fa)).toEqual(shape(en));
  });

  it("no 'not connected' statement is left in the contact section", () => {
    expect(strings(en.contact).filter((s) => /not (yet )?connected|once the form is connected/i.test(s))).toEqual([]);
    expect(strings(fa.contact).filter((s) => /وصل نیست|وصل شود/.test(s))).toEqual([]);
  });

  it("no 'not connected' statement is left anywhere on the site", () => {
    expect(strings(en).filter((s) => /not (yet )?connected|once the contact form is connected/i.test(s))).toEqual([]);
    expect(strings(fa).filter((s) => /فرم تماس هنوز وصل|وقتی فرم تماس وصل شود|فرم هنوز وصل/.test(s))).toEqual([]);
  });

  it("the footer names the one third-party script (Turnstile on the contact page)", () => {
    expect(en.footer.note).toMatch(/except the Cloudflare Turnstile check on the contact page/);
    expect(fa.footer.note).toMatch(/Cloudflare Turnstile/);
  });

  it("privacyNote names Turnstile, Cloudflare and 180 days in both languages", () => {
    expect(en.contact.form.privacyNote).toMatch(/Turnstile/);
    expect(en.contact.form.privacyNote).toMatch(/Cloudflare/);
    expect(en.contact.form.privacyNote).toMatch(/180 days/);
    expect(fa.contact.form.privacyNote).toMatch(/Turnstile/);
    expect(fa.contact.form.privacyNote).toMatch(/Cloudflare/);
    expect(fa.contact.form.privacyNote).toMatch(/۱۸۰ روز/);
  });

  it("the 180-day promise covers only the stored copy", () => {
    expect(en.contact.form.privacyNote).toMatch(/delete the stored copy after 180 days/);
    expect(fa.contact.form.privacyNote).toMatch(/نسخهٔ ذخیره‌شده را پس از ۱۸۰ روز حذف می‌کنیم/);
  });

  it("retry, the Turnstile load error, the Turnstile error and the new field errors exist in both languages", () => {
    for (const d of [en, fa]) {
      expect(d.contact.form.turnstileLoadFailed).toBeTruthy();
      expect(d.contact.form.turnstileError).toBeTruthy();
      expect(d.contact.form.retry).toBeTruthy();
      expect(d.contact.form.errors.phone).toBeTruthy();
      expect(d.contact.form.errors.minutes).toBeTruthy();
    }
  });

  it("volume is gone and minutes options match the schema", () => {
    for (const d of [en, fa]) {
      expect(d.contact.form).not.toHaveProperty("volume");
      expect(d.contact.form).not.toHaveProperty("volumeOptions");
      expect(d.contact.form.minutesOptions.map((o) => o.value)).toEqual(["", "lt10k", "10k-50k", "50k-250k", "250k-1m", "gt1m"]);
    }
  });
});
