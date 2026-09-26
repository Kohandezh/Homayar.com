import { useI18n } from "@/lib/i18n-context";
import { formatAudioSeconds } from "@/lib/format";
import type { BillingUsageSnapshot } from "@/types/platform";

const COPY = {
  en: {
    exhausted: "The audio processing allowance is used up; new uploads will be refused until it is extended.",
    total: (limit: string) => `Allowance: ${limit} of audio processing in total.`,
    reserved: (reserved: string) =>
      `${reserved} is held for recordings still being processed; the server settles it as used or releases it when processing ends.`,
    contactSales: "Contact sales to extend it",
  },
  fa: {
    exhausted: "سهمیهٔ پردازش صوت تمام شده است؛ تا تمدید آن، بارگذاری‌های تازه پذیرفته نمی‌شوند.",
    total: (limit: string) => `سهمیه: در مجموع ${limit} پردازش صوت.`,
    reserved: (reserved: string) =>
      `${reserved} برای فایل‌های در حال پردازش رزرو شده است؛ سرور پس از پایان پردازش آن را مصرف‌شده ثبت یا آزاد می‌کند.`,
    contactSales: "برای تمدید با فروش تماس بگیرید",
  },
} as const;

/**
 * Plain-language reading of the server's audio_seconds entitlement. Numbers
 * come only from the API and are floored to whole seconds; nothing here
 * enforces or predicts the limit.
 */
export default function AllowanceNotice({ item }: { item: BillingUsageSnapshot | undefined }) {
  const { lang } = useI18n();
  const copy = COPY[lang];
  if (!item || item.metric !== "audio_seconds" || item.limit === null) return null;
  const exhausted = item.remaining !== null && item.remaining <= 0;
  return (
    <div className={`allowance-notice${exhausted ? " is-exhausted" : ""}`} role={exhausted ? "alert" : undefined}>
      {exhausted && <p>{copy.exhausted}</p>}
      {!item.period_end && <p>{copy.total(formatAudioSeconds(item.limit, lang))}</p>}
      {item.reserved > 0 && <p>{copy.reserved(formatAudioSeconds(item.reserved, lang))}</p>}
      {exhausted && <a href="/contact">{copy.contactSales}</a>}
    </div>
  );
}
