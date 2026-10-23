/**
 * Every system health text, in the console's two languages (HOM-232).
 * Values the API sends as identifiers (task names, SHAs, revisions,
 * versions, failure classes, raw status strings) are passed through
 * `isolateLtr` by the components, never baked into this copy.
 */
export const SYSTEM_HEALTH_COPY = {
  en: {
    eyebrow: "Administration",
    pageTitle: "System health",
    loading: "Loading system health",
    tryAgain: "Try again",
    refresh: "Refresh",
    checkedAgo: (duration: string) => `Checked ${duration} ago`,
    ago: (duration: string) => `${duration} ago`,
    notReported: "Not reported",
    runbook: "Runbook",

    status: "Status",
    dependenciesTitle: "Dependencies",
    database: "Database",
    redis: "Redis",
    storage: "Object storage",
    schema: "Schema",
    codeHead: "Code head",
    databaseRevision: "Database revision",
    probeSeconds: (value: string) => `${value} s`,

    queueTitle: "Processing queue",
    queuedDue: "Queued due",
    oldestQueuedAge: "Oldest queued age",
    retryScheduled: "Retry scheduled",
    brokerQueueLength: "Broker queue length",

    sweepsTitle: "Periodic sweeps",
    lastSuccess: "Last success",
    expectedInterval: "Expected interval",
    failureClass: "Last failure class",

    backupTitle: "Backup",
    backupPostgres: "PostgreSQL",
    backupObjects: "Objects",
    backupOffsite: "Offsite",

    auditTitle: "Audit sink",
    lastVerify: "Last verify",
    lastSuccessVerify: "Last successful verify",

    buildTitle: "Build",
    buildVersion: "Version",
    buildSourceSha: "Source SHA",
    buildMigrationHead: "Migration head",
    buildProfile: "Profile",
    buildPythonVersion: "Python version",
  },
  fa: {
    eyebrow: "مدیریت",
    pageTitle: "سلامت سامانه",
    loading: "در حال بارگذاری سلامت سامانه",
    tryAgain: "تلاش دوباره",
    refresh: "بازخوانی",
    checkedAgo: (duration: string) => `بررسی‌شده ${duration} پیش`,
    ago: (duration: string) => `${duration} پیش`,
    notReported: "گزارش نشده",
    runbook: "راهنمای رفع مشکل",

    status: "وضعیت",
    dependenciesTitle: "وابستگی‌ها",
    database: "پایگاه داده",
    redis: "Redis",
    storage: "فضای ذخیره‌سازی",
    schema: "اسکیما",
    codeHead: "SHA کد",
    databaseRevision: "SHA پایگاه داده",
    probeSeconds: (value: string) => `${value} ثانیه`,

    queueTitle: "صف پردازش",
    queuedDue: "در صف اجرا",
    oldestQueuedAge: "قدیمی‌ترین مورد صف",
    retryScheduled: "زمان‌بندی برای تلاش دوباره",
    brokerQueueLength: "طول صف کارگزار",

    sweepsTitle: "کارهای دوره‌ای",
    lastSuccess: "آخرین موفقیت",
    expectedInterval: "بازهٔ مورد انتظار",
    failureClass: "آخرین ردهٔ خطا",

    backupTitle: "پشتیبان‌گیری",
    backupPostgres: "PostgreSQL",
    backupObjects: "اشیاء",
    backupOffsite: "خارج از سایت",

    auditTitle: "مقصد گزارش رویدادها",
    lastVerify: "آخرین راستی‌آزمایی",
    lastSuccessVerify: "آخرین راستی‌آزمایی موفق",

    buildTitle: "نسخهٔ نصب‌شده",
    buildVersion: "نسخه",
    buildSourceSha: "SHA کد منبع",
    buildMigrationHead: "آخرین مهاجرت",
    buildProfile: "نمایه",
    buildPythonVersion: "نسخهٔ پایتون",
  },
} as const;
