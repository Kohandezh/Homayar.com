import type { TimelineLane, TimelineReviewReason } from "@/types/timeline";

export interface TimelineCopy {
  title: string;
  loading: string;
  failed: string;
  empty: string;
  kinds: Record<TimelineLane["kind"], string>;
  unknownSpeaker: string;
  turnCount: (count: string) => string;
  overlapsList: string;
  overlapItem: (start: string, end: string, labels: string) => string;
  overlapTotal: (time: string) => string;
  unknownSpeechTotal: (time: string) => string;
  overlapBasis: string;
  demoData: string;
  reviewHeading: string;
  goToTurn: (start: string) => string;
  correctTurn: (start: string) => string;
  turnName: (label: string, start: string, end: string) => string;
  needsReview: (reasons: string) => string;
  reasons: Record<TimelineReviewReason, string>;
  reasonJoin: string;
  labelJoin: string;
}

export const TIMELINE_COPY: Record<"fa" | "en", TimelineCopy> = {
  en: {
    title: "Speaker timeline",
    loading: "Loading the speaker timeline",
    failed: "The speaker timeline could not be loaded.",
    empty: "This meeting has no speaker turns yet.",
    kinds: { identified: "Identified", anonymous: "Unnamed", unknown: "Unknown" },
    unknownSpeaker: "Unknown speaker",
    turnCount: (count) => `${count} turn${count === "1" ? "" : "s"}`,
    overlapsList: "Overlapping speech",
    overlapItem: (start, end, labels) => `Overlap from ${start} to ${end}: ${labels}`,
    overlapTotal: (time) => `Total overlap ${time}`,
    unknownSpeechTotal: (time) => `Unknown speech ${time}`,
    overlapBasis: "Overlap is measured from turn times.",
    demoData: "Demo data",
    reviewHeading: "Turns to review",
    goToTurn: (start) => `Go to turn at ${start}`,
    correctTurn: (start) => `Correct speaker of turn at ${start}`,
    turnName: (label, start, end) => `${label}, ${start} to ${end}`,
    needsReview: (reasons) => `, needs review: ${reasons}`,
    reasons: {
      unknown_speaker: "unknown speaker",
      overlap: "overlapping speech",
      outside_recording: "after the recording ends",
    },
    reasonJoin: ", ",
    labelJoin: ", ",
  },
  fa: {
    title: "خط زمانی گوینده‌ها",
    loading: "در حال دریافت خط زمانی",
    failed: "خط زمانی گوینده‌ها دریافت نشد.",
    empty: "هنوز نوبت گفت‌وگویی برای این جلسه ثبت نشده است.",
    kinds: { identified: "شناخته‌شده", anonymous: "بی‌نام", unknown: "نامشخص" },
    unknownSpeaker: "گویندهٔ نامشخص",
    turnCount: (count) => `${count} نوبت`,
    overlapsList: "هم‌زمانی گفتار",
    overlapItem: (start, end, labels) => `هم‌زمانی از ${start} تا ${end}: ${labels}`,
    overlapTotal: (time) => `مجموع هم‌زمانی ${time}`,
    unknownSpeechTotal: (time) => `گفتار نامشخص ${time}`,
    overlapBasis: "هم‌زمانی از روی زمان نوبت‌ها حساب شده است.",
    demoData: "دادهٔ نمایشی",
    reviewHeading: "نوبت‌های نیازمند بررسی",
    goToTurn: (start) => `رفتن به نوبت ${start}`,
    correctTurn: (start) => `اصلاح گویندهٔ نوبت ${start}`,
    turnName: (label, start, end) => `${label}، ${start} تا ${end}`,
    needsReview: (reasons) => `، نیاز به بررسی: ${reasons}`,
    reasons: {
      unknown_speaker: "گوینده نامشخص",
      overlap: "هم‌زمانی گفتار",
      outside_recording: "بعد از پایان فایل صوتی",
    },
    reasonJoin: "، ",
    labelJoin: "، ",
  },
};
