import { Fragment, type ReactNode } from "react";
import type { Locale } from "./i18n";

const token = "\\[?[A-Za-z0-9](?:[A-Za-z0-9_\\-.+/]*[A-Za-z0-9\\]])?";
const latinRun = new RegExp(`${token}(?: ${token})*`, "g");

export function bidi(text: string, locale: Locale): ReactNode {
  if (locale !== "fa" || !/[A-Za-z0-9]/.test(text)) return text;
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const match of text.matchAll(latinRun)) {
    const start = match.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    out.push(
      <bdi key={i++} dir="ltr" lang="en">
        {match[0]}
      </bdi>,
    );
    last = start + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.map((node, idx) => <Fragment key={idx}>{node}</Fragment>);
}
