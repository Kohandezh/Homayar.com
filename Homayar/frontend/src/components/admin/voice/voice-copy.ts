import type { ErrorCodeMessages } from "@/lib/error-messages";

/**
 * Every voice capacity text, in the console's two languages (HOM-194).
 * A node id is Latin text: a caller that shows a sentence with it passes
 * `isolateLtr(nodeId)`, so the id keeps its order in Persian. The button
 * labels (`drainNode`, `resumeNode`) are accessible names only, never shown.
 */
export const VOICE_COPY = {
  en: {
    cardTitle: "Voice capacity",
    concurrentCalls: "Concurrent calls",
    allNodes: "All nodes",
    callsOff: "Voice calls are off for this workspace.",

    eyebrow: "Administration",
    pageTitle: "Voice operations",
    loading: "Loading voice capacity",
    tryAgain: "Try again",
    workspaceCapacity: "Workspace capacity",
    deploymentCapacity: "Deployment capacity",
    operatorsOnly: "Deployment details are only shown to platform operators.",
    mediaNodes: "Media nodes",
    tableScrollLabel: "Node list, scrolls horizontally",
    noNodes: "No media nodes are registered.",

    columnNode: "Node",
    columnHealth: "Health",
    columnHeartbeat: "Last heartbeat",
    columnCalls: "Calls",
    columnStatus: "Status",
    columnActions: "Actions",
    healthy: "Healthy",
    unhealthy: "Unhealthy",
    draining: "Draining",
    active: "Active",
    secondsAgo: (n: string) => `${n} s ago`,
    minutesAgo: (n: string) => `${n} min ago`,
    hoursAgo: (n: string) => `${n} h ago`,
    drain: "Drain",
    resume: "Resume",
    drainNode: (nodeId: string) => `Drain ${nodeId}`,
    resumeNode: (nodeId: string) => `Resume ${nodeId}`,

    drainHeading: (nodeId: string) => `Drain ${nodeId}?`,
    drainBody: "New calls will not go to this node. Calls on it now keep running.",
    drainConfirm: "Drain node",
    resumeHeading: (nodeId: string) => `Resume ${nodeId}?`,
    resumeBody: "The node takes new calls again.",
    resumeConfirm: "Resume node",
    cancel: "Cancel",
    drained: (nodeId: string) => `${nodeId} is draining.`,
    resumed: (nodeId: string) => `${nodeId} takes calls again.`,
  },
  fa: {
    cardTitle: "ظرفیت تماس صوتی",
    concurrentCalls: "تماس‌های هم‌زمان",
    allNodes: "همهٔ گره‌ها",
    callsOff: "تماس صوتی برای این فضای کاری خاموش است.",

    eyebrow: "مدیریت",
    pageTitle: "عملیات صوتی",
    loading: "در حال بارگذاری ظرفیت صوتی",
    tryAgain: "تلاش دوباره",
    workspaceCapacity: "ظرفیت فضای کاری",
    deploymentCapacity: "ظرفیت استقرار",
    operatorsOnly: "جزئیات استقرار فقط به اپراتورهای پلتفرم نشان داده می‌شود.",
    mediaNodes: "گره‌های رسانه",
    tableScrollLabel: "فهرست گره‌ها، با پیمایش افقی",
    noNodes: "هیچ گره رسانه‌ای ثبت نشده است.",

    columnNode: "گره",
    columnHealth: "سلامت",
    columnHeartbeat: "آخرین ضربان",
    columnCalls: "تماس‌ها",
    columnStatus: "وضعیت",
    columnActions: "اقدام‌ها",
    healthy: "سالم",
    unhealthy: "ناسالم",
    draining: "در حال تخلیه",
    active: "فعال",
    secondsAgo: (n: string) => `${n} ثانیه پیش`,
    minutesAgo: (n: string) => `${n} دقیقه پیش`,
    hoursAgo: (n: string) => `${n} ساعت پیش`,
    drain: "تخلیه",
    resume: "ازسرگیری",
    drainNode: (nodeId: string) => `تخلیه ${nodeId}`,
    resumeNode: (nodeId: string) => `ازسرگیری ${nodeId}`,

    drainHeading: (nodeId: string) => `${nodeId} تخلیه شود؟`,
    drainBody: "تماس‌های تازه به این گره نمی‌روند. تماس‌های جاری ادامه پیدا می‌کنند.",
    drainConfirm: "تخلیه گره",
    resumeHeading: (nodeId: string) => `${nodeId} از سر گرفته شود؟`,
    resumeBody: "گره دوباره تماس تازه می‌گیرد.",
    resumeConfirm: "ازسرگیری گره",
    cancel: "انصراف",
    drained: (nodeId: string) => `${nodeId} در حال تخلیه است.`,
    resumed: (nodeId: string) => `${nodeId} دوباره تماس می‌گیرد.`,
  },
} as const;

/**
 * Error codes of POST /voice/nodes/{node_id}/drain with their own text. Every
 * other code (permission_denied, voice_state_error, 5xx) gets what
 * describeApiError says for it.
 */
export const DRAIN_ERROR_CODES: ErrorCodeMessages = {
  platform_operator_required: {
    fa: "فقط اپراتورهای پلتفرم می‌توانند گره را تخلیه کنند.",
    en: "Only platform operators can drain nodes.",
  },
  not_found: {
    fa: "این گره دیگر وجود ندارد.",
    en: "This node no longer exists.",
  },
};
