import type { ErrorCodeMessages } from "@/lib/error-messages";

/** Codes the billing endpoints answer with (backend/routers/v1/billing.py). */
export const BILLING_ERROR_CODES: ErrorCodeMessages = {
  billing_configuration_error: {
    fa: "صورتحساب این فضای کاری هنوز تنظیم نشده است. برای تکمیل آن با ما تماس بگیرید.",
    en: "Billing for this workspace is not set up yet. Contact us to finish the setup.",
  },
  invalid_usage_cursor: {
    fa: "فهرست رویدادها تغییر کرده است؛ از صفحهٔ اول دوباره ببینید.",
    en: "The event list has changed; start again from the first page.",
  },
};
