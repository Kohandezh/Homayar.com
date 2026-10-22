import type { Locale } from "@/lib/i18n";

// Review time calculator (/roi). Pure functions only, so the server render and the browser give the same text.

export type FieldKey = "agents" | "calls" | "minutesToday" | "minutesHomayar" | "rate";
export type ResultKey = "T" | "H1" | "H2" | "S" | "C";

export const fieldKeys: FieldKey[] = ["agents", "calls", "minutesToday", "minutesHomayar", "rate"];

// Upper bounds keep the numbers sane. 0 is valid for every field.
export const limits: Record<FieldKey, { max: number; whole: boolean }> = {
  agents: { max: 100000, whole: true },
  calls: { max: 100000, whole: true },
  minutesToday: { max: 600, whole: false },
  minutesHomayar: { max: 600, whole: false },
  rate: { max: 1000000000, whole: false },
};

// The worked example. Sample data, not a measurement (docs/website/claim-register.md, ROI rows).
export const example: Record<FieldKey, string> = {
  agents: "20",
  calls: "40",
  minutesToday: "12",
  minutesHomayar: "3",
  rate: "100",
};

export const MINUTES_PER_HOUR = 60;

export interface Parsed {
  // The number as Latin digits with no separators, for example "7.5". Used to compute exactly and to show the input in the formulas.
  text: string;
}

const persian = "۰۱۲۳۴۵۶۷۸۹";
const arabic = "٠١٢٣٤٥٦٧٨٩";

// Persian and Arabic-Indic digits become Latin, "٬" becomes "," and "٫" becomes ".".
function toLatin(raw: string): string {
  return raw.replace(/[۰-۹٠-٩٬٫]/g, (c) => {
    if (c === "٬") return ",";
    if (c === "٫") return ".";
    const p = persian.indexOf(c);
    return String(p >= 0 ? p : arabic.indexOf(c));
  });
}

// A thousands separator is accepted only in correct groups of three digits ("1,000"). "7,5" and "1,00" are invalid.
const plain = /^\d+(\.\d+)?$/;
const grouped = /^\d{1,3}(,\d{3})+(\.\d+)?$/;

export function parseField(raw: string, key: FieldKey): Parsed | null {
  const s = toLatin(raw.trim());
  if (!plain.test(s) && !grouped.test(s)) return null;
  const [intPart, fracPart] = s.replace(/,/g, "").split(".");
  const { max, whole } = limits[key];
  if (whole && fracPart !== undefined) return null;
  const int = intPart.replace(/^0+(?=\d)/, "");
  const frac = fracPart?.replace(/0+$/, "") ?? "";
  // Compared as whole numbers, so a value just above the bound (1000000000.01) is never rounded into range.
  const above = BigInt(int) > BigInt(max) || (BigInt(int) === BigInt(max) && frac !== "");
  if (above) return null;
  return { text: frac ? `${int}.${frac}` : int };
}

// Every result is a whole number of tenths (bigint), so the arithmetic is exact at any size and every shown
// line can be checked by hand. A float would lose digits above about 9 × 10^15.
export type Results = Record<ResultKey, bigint | null>;

// A decimal text as a whole number and a power of ten: "7.5" is 75 / 10.
function scaled(text: string): { n: bigint; scale: bigint } {
  const [int, frac = ""] = text.split(".");
  return { n: BigInt(int + frac), scale: BigInt(10) ** BigInt(frac.length) };
}

// a / b rounded half-up, for a >= 0 and b > 0.
function divHalfUp(a: bigint, b: bigint): bigint {
  return (BigInt(2) * a + b) / (BigInt(2) * b);
}

const TEN = BigInt(10);
const SIXTY = BigInt(MINUTES_PER_HOUR);

// Each line uses the shown (rounded) values of the lines before it. Hours and cost are rounded half-up to one decimal place.
// A result is null (shown as a dash) when an input it depends on is invalid.
export function compute(p: Record<FieldKey, Parsed | null>): Results {
  const n = p.agents ? BigInt(p.agents.text) : null;
  const k = p.calls ? BigInt(p.calls.text) : null;
  const m1 = p.minutesToday ? scaled(p.minutesToday.text) : null;
  const m2 = p.minutesHomayar ? scaled(p.minutesHomayar.text) : null;
  const w = p.rate ? scaled(p.rate.text) : null;
  const T = n !== null && k !== null ? n * k * TEN : null;
  const H1 = T !== null && m1 !== null ? divHalfUp(T * m1.n, SIXTY * m1.scale) : null;
  const H2 = T !== null && m2 !== null ? divHalfUp(T * m2.n, SIXTY * m2.scale) : null;
  const S = H1 !== null && H2 !== null ? (H1 > H2 ? H1 - H2 : BigInt(0)) : null;
  const C = S !== null && w !== null ? divHalfUp(S * w.n, w.scale) : null;
  return { T, H1, H2, S, C };
}

// Tenths as decimal text: 1205 is "120.5", 1200 is "120".
export function tenthsText(t: bigint): string {
  const int = t / TEN;
  const frac = t % TEN;
  return frac === BigInt(0) ? int.toString() : `${int}.${frac}`;
}

function group(int: string, sep: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, sep);
}

// Locale digits: "12,000.5" on /en, "۱۲٬۰۰۰٫۵" on /fa (the Intl "fa-IR" style). A whole number has no decimal part.
export function formatText(text: string, locale: Locale): string {
  const [int, frac] = text.split(".");
  const fa = locale === "fa";
  const out = group(int, fa ? "٬" : ",") + (frac ? (fa ? "٫" : ".") + frac : "");
  return fa ? out.replace(/[0-9]/g, (d) => persian[Number(d)]) : out;
}

export function formatTenths(t: bigint, locale: Locale): string {
  return formatText(tenthsText(t), locale);
}

// The example values as the visitor sees them in the fields: Persian digits on /fa.
export function exampleText(key: FieldKey, locale: Locale): string {
  return formatText(example[key], locale);
}
