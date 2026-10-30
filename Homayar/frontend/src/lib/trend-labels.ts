/**
 * The family and issue names of trend definition v1
 * (backend/issue_trends/definitions/trends.v1.json), copied as data: no API
 * serves them yet. An id the table does not know is shown as sent.
 */
import type { Lang } from "@/lib/i18n-context";

interface TrendName {
  en: string;
  fa: string;
}

const FAMILY_NAMES: Record<string, TrendName> = {
  call_reason: { en: "Call reasons", fa: "دلایل تماس" },
  unresolved_outcome: { en: "Unresolved outcomes", fa: "نتیجه‌های حل‌نشده" },
  service_complaint: { en: "Product and service complaints", fa: "شکایت از محصول یا خدمت" },
  qa: { en: "Quality checks", fa: "کنترل کیفیت" },
};

const ISSUE_NAMES: Record<string, TrendName> = {
  intent_request_support: { en: "Support request", fa: "درخواست پشتیبانی" },
  intent_report_dissatisfaction: { en: "Complaint", fa: "ثبت شکایت" },
  intent_purchase_inquiry: { en: "Purchase inquiry", fa: "استعلام خرید" },
  intent_resolve_billing: { en: "Billing issue", fa: "مسئله صورتحساب" },
  intent_schedule_appointment: { en: "Appointment", fa: "تعیین نوبت" },
  intent_request_callback: { en: "Callback request", fa: "درخواست تماس مجدد" },
  intent_cancel_service: { en: "Cancellation request", fa: "درخواست لغو سرویس" },
  intent_unsolicited: { en: "Unsolicited contact", fa: "تماس ناخواسته" },
  outcome_unresolved: { en: "Unresolved", fa: "حل نشده" },
  outcome_escalated: { en: "Escalated", fa: "ارجاع شده" },
  outcome_follow_up_scheduled: { en: "Follow-up scheduled", fa: "پیگیری برنامه‌ریزی شده" },
  complaint_pricing: { en: "Complaint about pricing", fa: "شکایت درباره قیمت" },
  complaint_refund: { en: "Complaint about refund", fa: "شکایت درباره بازگشت وجه" },
  complaint_invoice: { en: "Complaint about invoice", fa: "شکایت درباره فاکتور" },
  complaint_payment: { en: "Complaint about payment", fa: "شکایت درباره پرداخت" },
  complaint_delivery: { en: "Complaint about delivery", fa: "شکایت درباره ارسال و تحویل" },
  complaint_account_access: { en: "Complaint about account access", fa: "شکایت درباره دسترسی به حساب" },
  complaint_connectivity: { en: "Complaint about connectivity", fa: "شکایت درباره اتصال و شبکه" },
  complaint_contract: { en: "Complaint about contract and subscription", fa: "شکایت درباره قرارداد و اشتراک" },
  complaint_installation: { en: "Complaint about installation", fa: "شکایت درباره نصب و راه‌اندازی" },
  complaint_scheduling: { en: "Complaint about scheduling", fa: "شکایت درباره زمان‌بندی" },
  complaint_technical_fault: { en: "Complaint about technical fault", fa: "شکایت درباره خرابی فنی" },
};

export function trendFamilyLabel(id: string, lang: Lang): string {
  return Object.hasOwn(FAMILY_NAMES, id) ? FAMILY_NAMES[id][lang] : id;
}

export function trendIssueLabel(id: string, lang: Lang): string {
  return Object.hasOwn(ISSUE_NAMES, id) ? ISSUE_NAMES[id][lang] : id;
}
