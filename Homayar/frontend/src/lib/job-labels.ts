/**
 * Labels for the processing job fields the admin list shows: the job stage
 * and the failure class. Values come from backend/jobs (states.py,
 * failures.py, processor.py). An unknown value is shown as sent, never
 * guessed. The stage runs of an attempt (materialize, asr, ...) are named by
 * PROCESSING_COPY in platform/processing-copy.ts; a job's own `stage` is one
 * of the values below.
 */
import { statusLabel } from "@/lib/status-labels";

type Lang = "fa" | "en";

/** Job stages that are not already named by `statusLabel`. */
const JOB_STAGE_LABELS: Record<string, { fa: string; en: string }> = {
  retry_scheduled: { fa: "تلاش خودکار بعدی زمان‌بندی شد", en: "Automatic retry scheduled" },
  replay_scheduled: { fa: "پردازش دوباره زمان‌بندی شد", en: "Replay scheduled" },
  recovery_scheduled: { fa: "بازیابی زمان‌بندی شد", en: "Recovery scheduled" },
};

export function jobStageLabel(stage: string, lang: Lang): string {
  return JOB_STAGE_LABELS[stage]?.[lang] ?? statusLabel(stage, lang);
}

/** `failure_class` of backend/jobs/failures.py (WP06-T05). */
const FAILURE_CLASS_LABELS: Record<string, { fa: string; en: string }> = {
  publication: { fa: "ارسال به صف", en: "Queue publication" },
  transient: { fa: "خطای گذرا", en: "Temporary" },
  permanent: { fa: "خطای دائمی", en: "Permanent" },
  abandoned: { fa: "رهاشده", en: "Abandoned" },
  deadline: { fa: "مهلت تمام‌شده", en: "Deadline passed" },
  cancelled: { fa: "لغوشده", en: "Cancelled" },
};

export function failureClassLabel(failureClass: string, lang: Lang): string {
  return FAILURE_CLASS_LABELS[failureClass]?.[lang] ?? failureClass.replaceAll("_", " ");
}
