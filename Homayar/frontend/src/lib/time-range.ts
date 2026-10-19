import { toAsciiDigits } from "@/lib/format";

/**
 * A person-facing time code: `m:ss`, `h:mm:ss` (two digits after each colon) or whole seconds
 * (Persian digits accepted), as milliseconds. Null when the text is blank or
 * not such a time; the caller decides what an empty field means.
 */
export function parseTimecodeToMs(value: string): number | null {
  const ascii = toAsciiDigits(value.trim());
  if (!ascii) return null;
  const match = /^(\d+)(?::(\d{2}))?(?::(\d{2}))?$/.exec(ascii);
  if (!match) return null;
  const [first, second, third] = [Number(match[1]), match[2], match[3]];
  if (second === undefined) return first * 1000;
  if (third === undefined) {
    if (Number(second) > 59) return null;
    return (first * 60 + Number(second)) * 1000;
  }
  if (Number(second) > 59 || Number(third) > 59) return null;
  return (first * 3600 + Number(second) * 60 + Number(third)) * 1000;
}
