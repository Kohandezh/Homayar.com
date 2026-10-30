/**
 * Every string of the scorecards page (HOM-248), in both console languages.
 * The English strings are the contract the tests look for; keep the Persian
 * translations natural, not word by word. Numbers arrive already formatted
 * (formatNumber), so Persian gets Persian digits.
 */
import type { Applicability } from "@/components/admin/scorecards/scorecard-model";
import type { Lang } from "@/lib/i18n-context";
import type {
  ConversationDirection,
  ConversationType,
  ScorecardCheckKind,
  ScorecardScopeKind,
  SpeakerRole,
} from "@/types/scorecards";

/** The 422 codes of POST /scorecards/{key}/versions and /scorecards/validate. */
export const SCORECARD_INVALID_CODES = [
  "scorecard_invalid",
  "criterion_invalid",
  "duplicate_criterion",
  "weights_invalid",
  "weight_zero_not_auto_fail",
  "check_invalid",
  "check_id_mismatch",
  "na_not_allowed",
] as const;
export type ScorecardInvalidCode = (typeof SCORECARD_INVALID_CODES)[number];

export interface ScorecardsCopy {
  eyebrow: string;
  heading: string;
  intro: string;
  loading: string;
  loadError: string;
  tryAgain: string;
  listLabel: string;
  empty: string;
  listVersion: (version: string) => string;
  pickHint: string;
  newScorecard: string;
  edit: string;
  readOnlyHint: string;
  notFound: string;
  versionNotFound: string;
  detailError: string;
  viewingOld: (version: string, current: string) => string;
  viewCurrent: string;
  published: (version: string) => string;
  valid: string;
  /** The read view. */
  passThreshold: string;
  percent: (value: string) => string;
  criteria: string;
  weight: string;
  autoFail: string;
  naAllowedNote: string;
  appliesToEverything: string;
  appliesTo: string;
  allTypes: string;
  allDirections: string;
  types: Record<ConversationType, string>;
  directions: Record<ConversationDirection, string>;
  kinds: Record<ScorecardCheckKind, string>;
  speakers: Record<SpeakerRole, string>;
  scopes: Record<ScorecardScopeKind, string>;
  scopeWithTurns: (scope: string, turns: string) => string;
  question: string;
  phrases: string;
  triggerPhrases: string;
  speaker: string;
  scope: string;
  /** The version history. */
  history: string;
  columns: { version: string; name: string; reason: string; publishedBy: string; date: string };
  actions: string;
  current: string;
  viewVersion: (version: string) => string;
  historyError: string;
  /** The editor. */
  editorNew: string;
  editorEdit: (version: string) => string;
  key: string;
  keyHint: string;
  keyInvalid: string;
  name: string;
  description: string;
  thresholdHint: string;
  thresholdInvalid: string;
  reason: string;
  reasonInvalid: string;
  reasonRequired: string;
  criterion: (index: string) => string;
  criterionId: string;
  label: string;
  weightInvalid: string;
  naAllowed: string;
  conversationTypes: string;
  directionsLabel: string;
  groupHint: string;
  checkType: string;
  phrasesHint: string;
  triggerHint: string;
  turns: string;
  turnsInvalid: string;
  removeCriterion: string;
  addCriterion: string;
  check: string;
  publish: string;
  cancel: string;
  totalWeight: (total: string) => string;
  weightsMustAdd: string;
  conflict: (version: string) => string;
  conflictReplace: string;
  loadVersion: (version: string) => string;
  reloadFailed: string;
  publishFailed: string;
  checkFailed: string;
  serverDetail: string;
  invalid: Record<ScorecardInvalidCode, string>;
  /** The preview. */
  preview: string;
  previewIntro: string;
  callType: string;
  direction: string;
  unknownDirection: string;
  results: Record<Applicability, string>;
  weightCounts: (applicable: string, total: string) => string;
}

const en: ScorecardsCopy = {
  eyebrow: "Administration",
  heading: "Scorecards",
  intro:
    "A QA scorecard says what a good conversation looks like. Every change is published as a new version; published versions never change.",
  loading: "Loading scorecards",
  loadError: "The scorecards could not be loaded.",
  tryAgain: "Try again",
  listLabel: "All scorecards",
  empty: "No scorecards yet.",
  listVersion: (version) => `Version ${version}`,
  pickHint: "Choose a scorecard to see its criteria and version history.",
  newScorecard: "New scorecard",
  edit: "Edit",
  readOnlyHint: "Only workspace owners and admins can change scorecards.",
  notFound: "This scorecard was not found.",
  versionNotFound: "This version was not found.",
  detailError: "This scorecard could not be loaded.",
  viewingOld: (version, current) => `You are viewing version ${version}. The current version is ${current}.`,
  viewCurrent: "View current version",
  published: (version) => `Published version ${version}.`,
  valid: "The scorecard is valid.",
  passThreshold: "Pass threshold",
  percent: (value) => `${value}%`,
  criteria: "Criteria",
  weight: "Weight",
  autoFail: "Auto-fail",
  naAllowedNote: "Can be not applicable",
  appliesToEverything: "Applies to every conversation",
  appliesTo: "Applies to",
  allTypes: "all types",
  allDirections: "all directions",
  types: { call: "Call", meeting: "Meeting", voicemail: "Voicemail", other: "Other" },
  directions: { inbound: "Inbound", outbound: "Outbound", internal: "Internal" },
  kinds: {
    required_phrase: "Required phrase",
    forbidden_phrase: "Forbidden phrase",
    contextual: "Contextual question",
  },
  speakers: { agent: "Agent", customer: "Customer", any: "Any" },
  scopes: { whole_call: "Whole call", opening: "Opening", closing: "Closing" },
  scopeWithTurns: (scope, turns) => `${scope}, ${turns} turns`,
  question: "Question",
  phrases: "Phrases",
  triggerPhrases: "Trigger phrases",
  speaker: "Speaker",
  scope: "Scope",
  history: "Version history",
  columns: { version: "Version", name: "Name", reason: "Reason", publishedBy: "Published by", date: "Date" },
  actions: "Actions",
  current: "Current",
  viewVersion: (version) => `View version ${version}`,
  historyError: "The version history could not be loaded.",
  editorNew: "New scorecard",
  editorEdit: (version) => `New version based on version ${version}`,
  key: "Scorecard key",
  keyHint: "Lowercase letters, digits and _, starting with a letter. It cannot change later.",
  keyInvalid: "Use 2 to 64 characters: lowercase letters, digits and _, starting with a letter.",
  name: "Name",
  description: "Description",
  thresholdHint: "Percent, from 1 to 100.",
  thresholdInvalid: "Enter a whole number from 1 to 100.",
  reason: "Reason for this version",
  reasonInvalid: "Write a reason, up to 500 characters.",
  reasonRequired: "Write a reason to publish this version.",
  criterion: (index) => `Criterion ${index}`,
  criterionId: "Criterion ID",
  label: "Label",
  weightInvalid: "Enter a whole number from 0 to 100.",
  naAllowed: "Allow not applicable",
  conversationTypes: "Conversation types",
  directionsLabel: "Directions",
  groupHint: "None checked means all.",
  checkType: "Check type",
  phrasesHint: "One per line. Any one of them counts.",
  triggerHint: "One per line. Optional.",
  turns: "Turns",
  turnsInvalid: "Enter a whole number, 1 or more.",
  removeCriterion: "Remove criterion",
  addCriterion: "Add criterion",
  check: "Check",
  publish: "Publish",
  cancel: "Cancel",
  totalWeight: (total) => `Total weight: ${total} / 100`,
  weightsMustAdd: "Weights must add up to 100.",
  conflict: (version) => `Someone else published version ${version} while you were editing.`,
  conflictReplace: "Loading it replaces your draft with that version. Your reason stays.",
  loadVersion: (version) => `Load version ${version}`,
  reloadFailed: "That version could not be loaded. Your draft is unchanged.",
  publishFailed: "The scorecard could not be published.",
  checkFailed: "The scorecard could not be checked.",
  serverDetail: "Server detail:",
  invalid: {
    scorecard_invalid: "The scorecard is not valid.",
    criterion_invalid: "A criterion is not valid.",
    duplicate_criterion: "Two criteria have the same ID.",
    weights_invalid: "The weights must be whole numbers that add up to 100.",
    weight_zero_not_auto_fail: "A criterion with weight 0 must be auto-fail.",
    check_invalid: "A check is not valid.",
    check_id_mismatch: "A check ID does not match its criterion ID.",
    na_not_allowed: "A contextual question with trigger phrases must allow not applicable.",
  },
  preview: "Preview on a sample call",
  previewIntro: "Shows which criteria count for this kind of conversation.",
  callType: "Call type",
  direction: "Direction",
  unknownDirection: "Unknown",
  results: { applies: "Applies", not_applicable: "Not applicable", unknown: "Unknown" },
  weightCounts: (applicable, total) => `Weight that counts: ${applicable} of ${total}`,
};

const fa: ScorecardsCopy = {
  eyebrow: "مدیریت",
  heading: "کارت‌های امتیازدهی",
  intro:
    "کارت امتیازدهی مشخص می‌کند یک گفت‌وگوی خوب چه ویژگی‌هایی دارد. هر تغییر به شکل نسخه‌ای تازه منتشر می‌شود و نسخه‌های منتشرشده هرگز تغییر نمی‌کنند.",
  loading: "در حال بارگذاری کارت‌های امتیازدهی",
  loadError: "کارت‌های امتیازدهی بارگذاری نشد.",
  tryAgain: "تلاش دوباره",
  listLabel: "همه کارت‌های امتیازدهی",
  empty: "هنوز کارت امتیازدهی‌ای ساخته نشده است.",
  listVersion: (version) => `نسخه ${version}`,
  pickHint: "برای دیدن معیارها و تاریخچه نسخه‌ها، یک کارت امتیازدهی را انتخاب کنید.",
  newScorecard: "کارت امتیازدهی تازه",
  edit: "ویرایش",
  readOnlyHint: "فقط مالک و مدیران فضای کاری می‌توانند کارت‌های امتیازدهی را تغییر دهند.",
  notFound: "این کارت امتیازدهی پیدا نشد.",
  versionNotFound: "این نسخه پیدا نشد.",
  detailError: "این کارت امتیازدهی بارگذاری نشد.",
  viewingOld: (version, current) => `شما نسخه ${version} را می‌بینید. نسخه فعلی ${current} است.`,
  viewCurrent: "دیدن نسخه فعلی",
  published: (version) => `نسخه ${version} منتشر شد.`,
  valid: "کارت امتیازدهی معتبر است.",
  passThreshold: "حد قبولی",
  percent: (value) => `${value}٪`,
  criteria: "معیارها",
  weight: "وزن",
  autoFail: "رد خودکار",
  naAllowedNote: "می‌تواند «نامرتبط» باشد",
  appliesToEverything: "برای همه گفت‌وگوها",
  appliesTo: "برای",
  allTypes: "همه نوع‌ها",
  allDirections: "همه جهت‌ها",
  types: { call: "تماس", meeting: "جلسه", voicemail: "پیام صوتی", other: "سایر" },
  directions: { inbound: "ورودی", outbound: "خروجی", internal: "داخلی" },
  kinds: {
    required_phrase: "عبارت الزامی",
    forbidden_phrase: "عبارت ممنوع",
    contextual: "پرسش زمینه‌ای",
  },
  speakers: { agent: "کارشناس", customer: "مشتری", any: "هر کدام" },
  scopes: { whole_call: "کل تماس", opening: "آغاز تماس", closing: "پایان تماس" },
  scopeWithTurns: (scope, turns) => `${scope}، ${turns} نوبت`,
  question: "پرسش",
  phrases: "عبارت‌ها",
  triggerPhrases: "عبارت‌های آغازگر",
  speaker: "گوینده",
  scope: "محدوده",
  history: "تاریخچه نسخه‌ها",
  columns: { version: "نسخه", name: "نام", reason: "دلیل", publishedBy: "منتشرکننده", date: "تاریخ" },
  actions: "کارها",
  current: "فعلی",
  viewVersion: (version) => `دیدن نسخه ${version}`,
  historyError: "تاریخچه نسخه‌ها بارگذاری نشد.",
  editorNew: "کارت امتیازدهی تازه",
  editorEdit: (version) => `نسخه تازه بر پایه نسخه ${version}`,
  key: "کلید کارت امتیازدهی",
  keyHint: "حروف کوچک لاتین، رقم و _، که با یک حرف شروع شود. بعداً تغییر نمی‌کند.",
  keyInvalid: "۲ تا ۶۴ نویسه: حروف کوچک لاتین، رقم و _، که با یک حرف شروع شود.",
  name: "نام",
  description: "توضیح",
  thresholdHint: "درصد، از ۱ تا ۱۰۰.",
  thresholdInvalid: "یک عدد صحیح از ۱ تا ۱۰۰ وارد کنید.",
  reason: "دلیل این نسخه",
  reasonInvalid: "یک دلیل بنویسید، حداکثر ۵۰۰ نویسه.",
  reasonRequired: "برای انتشار این نسخه، یک دلیل بنویسید.",
  criterion: (index) => `معیار ${index}`,
  criterionId: "شناسه معیار",
  label: "عنوان",
  weightInvalid: "یک عدد صحیح از ۰ تا ۱۰۰ وارد کنید.",
  naAllowed: "اجازه «نامرتبط»",
  conversationTypes: "نوع گفت‌وگو",
  directionsLabel: "جهت تماس",
  groupHint: "اگر هیچ‌کدام انتخاب نشود، همه را شامل می‌شود.",
  checkType: "نوع بررسی",
  phrasesHint: "هر عبارت در یک خط. وجود یکی از آن‌ها کافی است.",
  triggerHint: "هر عبارت در یک خط. اختیاری.",
  turns: "تعداد نوبت",
  turnsInvalid: "یک عدد صحیح ۱ یا بیشتر وارد کنید.",
  removeCriterion: "حذف معیار",
  addCriterion: "افزودن معیار",
  check: "بررسی",
  publish: "انتشار",
  cancel: "انصراف",
  totalWeight: (total) => `مجموع وزن‌ها: ${total} از ۱۰۰`,
  weightsMustAdd: "مجموع وزن‌ها باید ۱۰۰ باشد.",
  conflict: (version) => `شخص دیگری هنگام ویرایش شما نسخه ${version} را منتشر کرد.`,
  conflictReplace: "بارگذاری آن نسخه، پیش‌نویس شما را با آن جایگزین می‌کند. دلیلی که نوشته‌اید می‌ماند.",
  loadVersion: (version) => `بارگذاری نسخه ${version}`,
  reloadFailed: "آن نسخه بارگذاری نشد. پیش‌نویس شما تغییری نکرده است.",
  publishFailed: "کارت امتیازدهی منتشر نشد.",
  checkFailed: "بررسی کارت امتیازدهی انجام نشد.",
  serverDetail: "جزئیات سرور:",
  invalid: {
    scorecard_invalid: "کارت امتیازدهی معتبر نیست.",
    criterion_invalid: "یکی از معیارها معتبر نیست.",
    duplicate_criterion: "دو معیار شناسه یکسان دارند.",
    weights_invalid: "وزن‌ها باید عدد صحیح باشند و مجموعشان ۱۰۰ شود.",
    weight_zero_not_auto_fail: "معیاری که وزنش صفر است باید «رد خودکار» باشد.",
    check_invalid: "یکی از بررسی‌ها معتبر نیست.",
    check_id_mismatch: "شناسه یک بررسی با شناسه معیارش یکی نیست.",
    na_not_allowed: "پرسش زمینه‌ای که عبارت آغازگر دارد باید اجازه «نامرتبط» داشته باشد.",
  },
  preview: "پیش‌نمایش روی یک تماس نمونه",
  previewIntro: "نشان می‌دهد کدام معیارها برای این نوع گفت‌وگو حساب می‌شوند.",
  callType: "نوع تماس",
  direction: "جهت",
  unknownDirection: "نامشخص",
  results: { applies: "حساب می‌شود", not_applicable: "نامرتبط", unknown: "نامشخص" },
  weightCounts: (applicable, total) => `وزنی که حساب می‌شود: ${applicable} از ${total}`,
};

export const SCORECARDS_COPY: Record<Lang, ScorecardsCopy> = { en, fa };

export function isScorecardInvalidCode(code: string | null): code is ScorecardInvalidCode {
  return code !== null && (SCORECARD_INVALID_CODES as readonly string[]).includes(code);
}
