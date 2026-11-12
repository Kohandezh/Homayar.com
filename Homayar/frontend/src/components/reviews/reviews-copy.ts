import type { ErrorCodeMessages } from "@/lib/error-messages";
import type { Lang } from "@/lib/i18n-context";
import type { ReviewCancelReason } from "@/types/reviews";

/**
 * Every string of the review queue pages (HOM-265), in both console
 * languages. The English strings are the contract the tests look for; keep
 * the Persian translations natural. Numbers arrive already formatted
 * (formatNumber), so Persian gets Persian digits.
 */
export interface ReviewsCopy {
  title: string;
  intro: string;
  loading: string;
  loadError: string;
  tryAgain: string;
  listLabel: string;
  /** Filters. */
  filterStatus: string;
  filterAssignee: string;
  filterOverdue: string;
  filterActive: string;
  filterAnyone: string;
  filterMe: string;
  filterUnassigned: string;
  open: string;
  /** The row's link to the item page; never "Open", which is a status name. */
  view: string;
  /** Load more. */
  loadMore: string;
  /** Columns. */
  columnStatus: string;
  columnDue: string;
  columnAssignee: string;
  columnTeam: string;
  columnSource: string;
  columnMeeting: string;
  columnActions: string;
  /** Empty list. */
  empty: string;
  emptyScope: string;
  /** Row values. */
  statuses: Record<"open" | "in_review" | "completed" | "cancelled", string>;
  overdue: string;
  unassigned: string;
  noTeam: string;
  sources: Record<"manual" | "sample", string>;
  openItem: string;
  openReport: string;
  /** Item page. */
  notFound: string;
  backToQueue: string;
  /** Names the item region; never the raw id. */
  itemRegionLabel: string;
  fieldStatus: string;
  fieldDue: string;
  fieldAssignee: string;
  fieldTeam: string;
  fieldSource: string;
  fieldRevision: string;
  completedAtLabel: string;
  completedAt: (value: string) => string;
  cancelledWith: (reason: string) => string;
  /** Assign. */
  assignHeading: string;
  assignLabel: string;
  assignSubmit: string;
  /** Due time. */
  dueHeading: string;
  dueLabel: string;
  dueSubmit: string;
  dueInvalid: string;
  /** Actions. */
  startReview: string;
  completeReview: string;
  cancelItem: string;
  cancelHeading: string;
  cancelBody: string;
  cancelReasonLabel: string;
  cancelReasons: Record<ReviewCancelReason, string>;
  cancelConfirm: string;
  cancelDialogCancel: string;
  /** After a write. */
  itemSaved: string;
  conflict: string;
  writeFailed: string;
  /** Done and next. */
  reviewCompleted: string;
  nextItem: string;
  findingNext: string;
  queueEmpty: string;
  queueEmptyLink: string;
  /** Criteria panel. */
  criteriaHeading: string;
  machineScore: string;
  humanScore: string;
  preview: string;
  final: string;
  outcome: string;
  score: string;
  passThreshold: string;
  weightsLine: (applicable: string, earned: string, failed: string, insufficient: string) => string;
  noMachine: string;
  criteriaLabel: string;
  columnCriterion: string;
  columnWeight: string;
  columnMachine: string;
  columnEvidence: string;
  columnDecision: string;
  autoFail: string;
  evidenceCount: (count: string) => string;
  notReviewable: string;
  noDecision: string;
  /** Decisions. */
  accept: string;
  acceptBlocked: string;
  override: string;
  reviewerOutcome: string;
  reasonCode: string;
  note: string;
  noteCounter: (count: string, max: string) => string;
  evidenceTurns: string;
  evidenceHint: string;
  saveDecision: string;
  cancelDecision: string;
  /** The "not chosen yet" option of the override selects. */
  choose: string;
  outcomes: Record<"passed" | "failed" | "not_applicable", string>;
  machineOutcomeInsufficient: string;
  overrideReasons: Record<
    "evidence_missed" | "evidence_misread" | "transcript_error" | "context_outside_call" | "policy_exception" | "other",
    string
  >;
  decisionAccepted: string;
  decisionSaved: string;
  /** Client validation of the override form. */
  validation: {
    outcomeRequired: string;
    reasonRequired: string;
    noteRequired: string;
    noteTooLong: string;
    evidenceRequired: string;
    evidenceInvalid: string;
    evidenceDuplicate: string;
    evidenceTooMany: string;
  };
  /** The unknown outcome or reason the server may still send. */
  unknownOutcome: (value: string) => string;
}

const en: ReviewsCopy = {
  title: "Review queue",
  intro: "The calls queued for supervisor review, with their due time and assignee.",
  loading: "Loading review items",
  loadError: "The review items could not be loaded.",
  tryAgain: "Try again",
  listLabel: "Review queue list",
  filterStatus: "Status",
  filterAssignee: "Assignee",
  filterOverdue: "Overdue only",
  filterActive: "Active",
  filterAnyone: "Anyone",
  filterMe: "Me",
  filterUnassigned: "Unassigned",
  open: "Open",
  view: "View",
  loadMore: "Load more",
  columnStatus: "Status",
  columnDue: "Due",
  columnAssignee: "Assignee",
  columnTeam: "Team",
  columnSource: "Source",
  columnMeeting: "Call",
  columnActions: "Actions",
  empty: "No review items match these filters.",
  emptyScope: "Items appear only for the teams you supervise.",
  statuses: { open: "Open", in_review: "In review", completed: "Completed", cancelled: "Cancelled" },
  overdue: "Overdue",
  unassigned: "Unassigned",
  noTeam: "No team",
  sources: { manual: "Manual", sample: "Sample" },
  openItem: "Open review item",
  openReport: "Open call report",
  notFound: "This review item does not exist or you cannot see it.",
  backToQueue: "Back to review queue",
  itemRegionLabel: "Review item",
  fieldStatus: "Status",
  fieldDue: "Due time",
  fieldAssignee: "Assignee",
  fieldTeam: "Team",
  fieldSource: "Source",
  fieldRevision: "Revision",
  completedAtLabel: "Completed at",
  completedAt: (value) => `Completed ${value}`,
  cancelledWith: (reason) => `Cancelled: ${reason}`,
  assignHeading: "Assign",
  assignLabel: "Assign to",
  assignSubmit: "Assign",
  dueHeading: "Due time",
  dueLabel: "Due time",
  dueSubmit: "Save due time",
  dueInvalid: "The due time must be later than now and at most 90 days ahead.",
  startReview: "Start review",
  completeReview: "Complete review",
  cancelItem: "Cancel item",
  cancelHeading: "Cancel this review item?",
  cancelBody: "The item closes as cancelled and cannot be reopened. A new item can be queued for the same call later.",
  cancelReasonLabel: "Reason",
  cancelReasons: { duplicate: "Duplicate", out_of_scope: "Out of scope", meeting_unusable: "Meeting unusable", other: "Other" },
  cancelConfirm: "Cancel item",
  cancelDialogCancel: "Cancel",
  itemSaved: "Saved.",
  conflict: "Someone changed this item. The latest version is loaded.",
  writeFailed: "The change was not saved.",
  reviewCompleted: "Review completed.",
  nextItem: "Next item",
  findingNext: "Finding the next item",
  queueEmpty: "Your queue is empty.",
  queueEmptyLink: "Back to review queue",
  criteriaHeading: "Criteria",
  machineScore: "Machine score",
  humanScore: "Human score",
  preview: "Preview",
  final: "Final",
  outcome: "Outcome",
  score: "Score",
  passThreshold: "Pass threshold",
  weightsLine: (applicable, earned, failed, insufficient) =>
    `Weights: ${applicable} applicable, ${earned} earned, ${failed} failed, ${insufficient} insufficient`,
  noMachine: "This call has no machine score to review.",
  criteriaLabel: "Criteria",
  columnCriterion: "Criterion",
  columnWeight: "Weight",
  columnMachine: "Machine",
  columnEvidence: "Evidence",
  columnDecision: "Decision",
  autoFail: "Auto-fail",
  evidenceCount: (count) => `${count} turn(s)`,
  notReviewable: "Not reviewable for this call.",
  noDecision: "—",
  accept: "Accept",
  acceptBlocked: "Insufficient evidence can only be overridden.",
  override: "Override",
  reviewerOutcome: "Reviewer outcome",
  reasonCode: "Reason code",
  note: "Note",
  noteCounter: (count, max) => `${count} / ${max}`,
  evidenceTurns: "Evidence turns",
  evidenceHint: "Comma-separated turn numbers of the transcript, for example 3, 7, 12.",
  saveDecision: "Save decision",
  cancelDecision: "Cancel",
  choose: "Choose…",
  outcomes: { passed: "Passed", failed: "Failed", not_applicable: "Not applicable" },
  machineOutcomeInsufficient: "Insufficient evidence",
  overrideReasons: {
    evidence_missed: "Evidence missed",
    evidence_misread: "Evidence misread",
    transcript_error: "Transcript error",
    context_outside_call: "Context outside the call",
    policy_exception: "Policy exception",
    other: "Other",
  },
  decisionAccepted: "Criterion accepted.",
  decisionSaved: "Decision saved.",
  validation: {
    outcomeRequired: "Choose the reviewer outcome.",
    reasonRequired: "Choose a reason code.",
    noteRequired: "Write a note explaining the override.",
    noteTooLong: "The note can be at most 2000 characters.",
    evidenceRequired: "List at least one evidence turn.",
    evidenceInvalid: "Evidence turns must be whole numbers of 0 or more.",
    evidenceDuplicate: "Evidence turns must be distinct.",
    evidenceTooMany: "At most 20 evidence turns are allowed.",
  },
  unknownOutcome: (value) => value,
};

const fa: ReviewsCopy = {
  title: "صف بازبینی",
  intro: "تماس‌های در صف بازبینی سرپرست، با مهلت و مسئول هر مورد.",
  loading: "در حال بارگذاری موارد بازبینی",
  loadError: "موارد بازبینی بارگذاری نشد.",
  tryAgain: "تلاش دوباره",
  listLabel: "فهرست صف بازبینی",
  filterStatus: "وضعیت",
  filterAssignee: "مسئول",
  filterOverdue: "فقط از موعد گذشته",
  filterActive: "فعال",
  filterAnyone: "همه",
  filterMe: "من",
  filterUnassigned: "بی‌مسئول",
  open: "باز کردن",
  view: "دیدن",
  loadMore: "بیشتر",
  columnStatus: "وضعیت",
  columnDue: "مهلت",
  columnAssignee: "مسئول",
  columnTeam: "تیم",
  columnSource: "منشأ",
  columnMeeting: "تماس",
  columnActions: "کارها",
  empty: "مورد بازبینی‌ای با این فیلترها پیدا نشد.",
  emptyScope: "موارد فقط برای تیم‌هایی نمایش داده می‌شوند که سرپرستشان هستید.",
  statuses: { open: "باز", in_review: "در حال بازبینی", completed: "کامل‌شده", cancelled: "لغوشده" },
  overdue: "از موعد گذشته",
  unassigned: "بی‌مسئول",
  noTeam: "بدون تیم",
  sources: { manual: "دستی", sample: "نمونه" },
  openItem: "باز کردن مورد بازبینی",
  openReport: "دیدن گزارش تماس",
  notFound: "این مورد بازبینی وجود ندارد یا شما اجازهٔ دیدن آن را ندارید.",
  backToQueue: "بازگشت به صف بازبینی",
  itemRegionLabel: "مورد بازبینی",
  fieldStatus: "وضعیت",
  fieldDue: "مهلت",
  fieldAssignee: "مسئول",
  fieldTeam: "تیم",
  fieldSource: "منشأ",
  fieldRevision: "نسخه",
  completedAtLabel: "زمان کامل‌شدن",
  completedAt: (value) => `کامل شد ${value}`,
  cancelledWith: (reason) => `لغو شد: ${reason}`,
  assignHeading: "تعیین مسئول",
  assignLabel: "واگذار به",
  assignSubmit: "تعیین",
  dueHeading: "مهلت",
  dueLabel: "مهلت",
  dueSubmit: "ذخیرهٔ مهلت",
  dueInvalid: "مهلت باید بعد از الان و حداکثر ۹۰ روز جلوتر باشد.",
  startReview: "شروع بازبینی",
  completeReview: "پایان بازبینی",
  cancelItem: "لغو مورد",
  cancelHeading: "این مورد بازبینی لغو شود؟",
  cancelBody: "مورد به حالت لغوشده می‌رود و دیگر باز نمی‌شود. بعداً می‌توان برای همان تماس مورد تازه‌ای ساخت.",
  cancelReasonLabel: "دلیل",
  cancelReasons: { duplicate: "تکراری", out_of_scope: "خارج از حوزه", meeting_unusable: "تماس غیرقابل استفاده", other: "سایر" },
  cancelConfirm: "لغو مورد",
  cancelDialogCancel: "انصراف",
  itemSaved: "ذخیره شد.",
  conflict: "شخص دیگری این مورد را تغییر داد. تازه‌ترین نسخه بارگذاری شد.",
  writeFailed: "تغییر ذخیره نشد.",
  reviewCompleted: "بازبینی کامل شد.",
  nextItem: "مورد بعدی",
  findingNext: "در حال یافتن مورد بعدی",
  queueEmpty: "صف شما خالی است.",
  queueEmptyLink: "بازگشت به صف بازبینی",
  criteriaHeading: "معیارها",
  machineScore: "امتیاز ماشین",
  humanScore: "امتیاز انسانی",
  preview: "پیش‌نمایش",
  final: "نهایی",
  outcome: "نتیجه",
  score: "امتیاز",
  passThreshold: "حد قبولی",
  weightsLine: (applicable, earned, failed, insufficient) =>
    `وزن‌ها: ${applicable} مرتبط، ${earned} کسب‌شده، ${failed} مردود، ${insufficient} با شواهد ناکافی`,
  noMachine: "این تماس امتیاز ماشینی برای بازبینی ندارد.",
  criteriaLabel: "معیارهای بازبینی",
  columnCriterion: "معیار",
  columnWeight: "وزن",
  columnMachine: "ماشین",
  columnEvidence: "شواهد",
  columnDecision: "تصمیم",
  autoFail: "رد خودکار",
  evidenceCount: (count) => `${count} نوبت`,
  notReviewable: "برای این تماس قابل بازبینی نیست.",
  noDecision: "—",
  accept: "پذیرش",
  acceptBlocked: "شواهد ناکافی را فقط می‌توان بازنویسی کرد.",
  override: "بازنویسی",
  reviewerOutcome: "نتیجهٔ بازبین",
  reasonCode: "کد دلیل",
  note: "یادداشت",
  noteCounter: (count, max) => `${count} / ${max}`,
  evidenceTurns: "نوبت‌های شاهد",
  evidenceHint: "شماره نوبت‌های گفت‌وگو با ویرگول، مثل ۳، ۷، ۱۲.",
  saveDecision: "ذخیرهٔ تصمیم",
  cancelDecision: "انصراف",
  choose: "انتخاب…",
  outcomes: { passed: "قبول", failed: "رد", not_applicable: "نامرتبط" },
  machineOutcomeInsufficient: "شواهد ناکافی",
  overrideReasons: {
    evidence_missed: "از شواهد جا ماند",
    evidence_misread: "شواهد اشتباه خوانده شد",
    transcript_error: "خطای متن گفت‌وگو",
    context_outside_call: "زمینهٔ بیرون از تماس",
    policy_exception: "استثنای خط‌مشی",
    other: "سایر",
  },
  decisionAccepted: "معیار پذیرفته شد.",
  decisionSaved: "تصمیم ذخیره شد.",
  validation: {
    outcomeRequired: "نتیجهٔ بازبین را انتخاب کنید.",
    reasonRequired: "یک کد دلیل انتخاب کنید.",
    noteRequired: "دلیل بازنویسی را بنویسید.",
    noteTooLong: "یادداشت حداکثر می‌تواند ۲۰۰۰ نویسه باشد.",
    evidenceRequired: "حداقل یک نوبت شاهد بنویسید.",
    evidenceInvalid: "نوبت‌های شاهد باید اعداد صحیح ۰ یا بزرگ‌تر باشند.",
    evidenceDuplicate: "نوبت‌های شاهد نباید تکراری باشند.",
    evidenceTooMany: "حداکثر ۲۰ نوبت شاهد مجاز است.",
  },
  unknownOutcome: (value) => value,
};

export const REVIEWS_COPY: Record<Lang, ReviewsCopy> = { en, fa };

/** The fa and en texts for every review-items error code (SPEC section 2). */
export const REVIEW_ERROR_CODES: ErrorCodeMessages = {
  review_item_not_found: {
    fa: "این مورد بازبینی وجود ندارد یا شما اجازهٔ دیدن آن را ندارید.",
    en: "This review item does not exist or you cannot see it.",
  },
  review_item_revision_conflict: {
    fa: "شخص دیگری این مورد را تغییر داد. تازه‌ترین نسخه بارگذاری شد.",
    en: "Someone changed this item. The latest version is loaded.",
  },
  review_item_closed: {
    fa: "این مورد بازبینی بسته شده است.",
    en: "This review item is already closed.",
  },
  review_item_state_invalid: {
    fa: "این کار با وضعیت فعلی مورد ممکن نیست.",
    en: "This change does not fit the item's current state.",
  },
  review_not_assignee: {
    fa: "این کار فقط با مسئول تعیین‌شدهٔ مورد ممکن است.",
    en: "Only the assignee of this item can do that.",
  },
  review_assignee_is_agent: {
    fa: "کارشناس این تماس نمی‌تواند مسئول بازبینی آن باشد.",
    en: "The agent of this call cannot be assigned its review.",
  },
  review_assignee_ineligible: {
    fa: "این فرد برای مسئول شدن این بازبینی واجد شرایط نیست.",
    en: "This person is not eligible to be assigned this review.",
  },
  review_due_at_invalid: {
    fa: "مهلت باید بعد از الان و حداکثر ۹۰ روز جلوتر باشد.",
    en: "The due time must be later than now and at most 90 days ahead.",
  },
  review_item_has_no_result: {
    fa: "این مورد بازبینی امتیاز ماشینی ندارد.",
    en: "This review item has no machine score.",
  },
  scorecard_version_unsupported: {
    fa: "نسخهٔ کارت امتیازدهی این نتیجه دیگر پشتیبانی نمی‌شود.",
    en: "The scorecard version of this result is no longer supported.",
  },
  criterion_not_found: {
    fa: "این معیار در نتیجهٔ ماشینی پیدا نشد.",
    en: "This criterion was not found in the machine result.",
  },
  criterion_not_reviewable: {
    fa: "برای این تماس قابل بازبینی نیست.",
    en: "Not reviewable for this call.",
  },
  criterion_accept_not_possible: {
    fa: "شواهد ناکافی را فقط می‌توان بازنویسی کرد.",
    en: "Insufficient evidence can only be overridden.",
  },
  override_matches_machine: {
    fa: "نتیجهٔ انتخاب‌شده همان نتیجهٔ ماشین است؛ برای تأیید از پذیرش استفاده کنید.",
    en: "The chosen outcome matches the machine; use Accept to confirm it.",
  },
  reviewer_na_not_allowed: {
    fa: "این معیار اجازهٔ «نامرتبط» ندارد.",
    en: "This criterion cannot be marked not applicable.",
  },
  override_reason_required: {
    fa: "برای بازنویسی، کد دلیل و یادداشت لازم است.",
    en: "An override needs a reason code and a note.",
  },
  override_evidence_required: {
    fa: "برای بازنویسی، بین ۱ تا ۲۰ نوبت شاهد لازم است.",
    en: "An override needs 1 to 20 evidence turns.",
  },
  review_decision_invalid: {
    fa: "این تصمیم پذیرفته نشد.",
    en: "This decision was not accepted.",
  },
  review_result_not_computable: {
    fa: "امتیاز انسانی این بازبینی حساب نشد.",
    en: "The human score of this review could not be computed.",
  },
};
