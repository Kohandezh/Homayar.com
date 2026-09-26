import type { ReactNode } from "react";
import { PLACEHOLDER_PATTERN, type ResultsCopy } from "@/components/platform/results-copy";

function placeholderClass(placeholder: string) {
  return placeholder.slice(1, placeholder.lastIndexOf("_")).toLowerCase();
}

/** Transcript text with masked values marked; revealed values replace them in memory only. */
export default function MaskedText({
  text,
  copy,
  revealed,
  onPick,
}: {
  text: string;
  copy: ResultsCopy;
  revealed: Record<string, string>;
  onPick?: (placeholder: string) => void;
}) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(PLACEHOLDER_PATTERN)) {
    const placeholder = match[0];
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    const label = copy.transcript.classes[placeholderClass(placeholder)] ?? placeholderClass(placeholder);
    if (revealed[placeholder] !== undefined) {
      parts.push(
        <mark key={index} className="pii-revealed" title={copy.transcript.revealed}>
          <bdi>{revealed[placeholder]}</bdi>
        </mark>,
      );
    } else if (onPick) {
      parts.push(
        <button
          key={index}
          type="button"
          className="pii-placeholder"
          onClick={() => onPick(placeholder)}
          aria-label={copy.transcript.revealTitle(placeholder)}
        >
          <bdi dir="ltr">{placeholder}</bdi>
        </button>,
      );
    } else {
      parts.push(
        <mark key={index} className="pii-placeholder" title={copy.transcript.placeholder(label)}>
          <bdi dir="ltr">{placeholder}</bdi>
        </mark>,
      );
    }
    last = index + placeholder.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

