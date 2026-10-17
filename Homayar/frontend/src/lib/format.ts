/** A clock-style duration such as 12:34 or 1:02:03; Persian digits in Persian. */
export function formatDuration(seconds: number, locale = "en") {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  const text = hours
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toString().padStart(2, "0")}`
    : `${minutes}:${remainder.toString().padStart(2, "0")}`;
  return locale === "fa" ? text.replace(/[0-9]/g, (digit) => "۰۱۲۳۴۵۶۷۸۹"[Number(digit)]) : text;
}

export function formatDate(value: string, locale: string) {
  return new Intl.DateTimeFormat(locale === "fa" ? "fa-IR" : "en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function numberLocale(locale: string) {
  return locale === "fa" ? "fa-IR" : "en";
}

export function formatNumber(value: number, locale: string, maximumFractionDigits = 0) {
  return new Intl.NumberFormat(numberLocale(locale), { maximumFractionDigits }).format(value);
}

/** A countdown such as 2:05, with Persian digits in Persian. */
export function formatClock(seconds: number, locale: string) {
  const total = Math.max(0, Math.ceil(seconds));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  const digits = new Intl.NumberFormat(numberLocale(locale), { minimumIntegerDigits: 2, useGrouping: false });
  return `${formatNumber(minutes, locale)}:${digits.format(remainder)}`;
}

/** Converts Persian and Arabic-Indic digits to ASCII digits. */
export function toAsciiDigits(value: string) {
  return value
    .replace(/[۰-۹]/g, (digit) => String(digit.charCodeAt(0) - 0x06f0))
    .replace(/[٠-٩]/g, (digit) => String(digit.charCodeAt(0) - 0x0660));
}

/**
 * A whole number typed with ASCII, Persian or Arabic-Indic digits; null when
 * the text is blank or not a whole number (never coerced to zero).
 */
export function parseWholeNumber(value: string): number | null {
  const ascii = toAsciiDigits(value.trim());
  return /^\d{1,6}$/.test(ascii) ? Number(ascii) : null;
}

/** A language code as a name in the interface language ("fa" -> "فارسی"); the code if unknown. */
export function languageName(code: string, locale: "fa" | "en") {
  try {
    return new Intl.DisplayNames([locale], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** ASCII digits in a string shown as Persian digits in Persian (no grouping). */
export function localizeDigits(value: string, locale: string) {
  return locale === "fa" ? value.replace(/[0-9]/g, (digit) => "۰۱۲۳۴۵۶۷۸۹"[Number(digit)]) : value;
}

/**
 * Wraps left-to-right data (an email, a phone number, a code) in Unicode
 * isolates so it keeps its order inside a right-to-left sentence.
 */
export function isolateLtr(value: string) {
  return `⁦${value}⁩`;
}

/** Audio seconds as minutes and seconds, e.g. "29 min 50 s" / "۲۹ دقیقه و ۵۰ ثانیه". */
export function formatAudioSeconds(seconds: number, locale: string) {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  if (locale === "fa") {
    if (!minutes) return `${formatNumber(remainder, locale)} ثانیه`;
    return remainder
      ? `${formatNumber(minutes, locale)} دقیقه و ${formatNumber(remainder, locale)} ثانیه`
      : `${formatNumber(minutes, locale)} دقیقه`;
  }
  if (!minutes) return `${remainder} s`;
  return remainder ? `${formatNumber(minutes, locale)} min ${remainder} s` : `${formatNumber(minutes, locale)} min`;
}

/**
 * Audio seconds as hours and minutes, e.g. "2 h 5 min" / "۲ ساعت و ۵ دقیقه".
 * Minutes are floored, so a quota is never shown as larger than it is. Under a
 * minute is "less than one minute", and a negative amount (an adjustment) is a
 * minus sign followed by the text of its magnitude.
 */
export function formatAudioHours(seconds: number, locale: string) {
  const sign = seconds < 0 ? "-" : "";
  const magnitude = Math.abs(seconds);
  const totalMinutes = Math.floor(magnitude / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (locale === "fa") {
    let text: string;
    if (magnitude > 0 && totalMinutes === 0) text = "کمتر از ۱ دقیقه";
    else if (hours === 0) text = `${formatNumber(minutes, locale)} دقیقه`;
    else if (minutes > 0) text = `${formatNumber(hours, locale)} ساعت و ${formatNumber(minutes, locale)} دقیقه`;
    else text = `${formatNumber(hours, locale)} ساعت`;
    return `${sign}${text}`;
  }
  let text: string;
  if (magnitude > 0 && totalMinutes === 0) text = "< 1 min";
  else if (hours === 0) text = `${formatNumber(minutes, locale)} min`;
  else if (minutes > 0) text = `${formatNumber(hours, locale)} h ${formatNumber(minutes, locale)} min`;
  else text = `${formatNumber(hours, locale)} h`;
  return `${sign}${text}`;
}

/** Bytes as KB/MB with locale digits. */
export function formatBytes(bytes: number, locale: string) {
  const units = locale === "fa" ? ["بایت", "کیلوبایت", "مگابایت", "گیگابایت"] : ["B", "KB", "MB", "GB"];
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${formatNumber(value, locale, unit === 0 ? 0 : 1)} ${units[unit]}`;
}
