import { Link } from "react-router";

import { SCORECARDS_COPY } from "@/components/admin/scorecards/scorecards-copy";
import { EmptyState } from "@/components/platform/Ui";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { ScorecardItem } from "@/types/scorecards";

/** One link per scorecard (its latest version); a link sets `?key=`. */
export default function ScorecardList({
  items,
  selectedKey,
  onSelect,
}: {
  items: ScorecardItem[];
  selectedKey: string | null;
  /** Called on a click, before the URL changes: the page closes its editor. */
  onSelect: () => void;
}) {
  const { lang } = useI18n();
  const copy = SCORECARDS_COPY[lang];
  if (items.length === 0) return <EmptyState label={copy.empty} />;

  return (
    <nav aria-label={copy.listLabel}>
      <ul className="m-0 grid gap-2 p-0">
        {items.map((item) => {
          const selected = item.scorecard_key === selectedKey;
          return (
            <li key={item.scorecard_key} className="list-none">
              <Link
                to={{ search: `?key=${encodeURIComponent(item.scorecard_key)}` }}
                aria-current={selected ? "page" : undefined}
                onClick={onSelect}
                className={`platform-surface grid gap-0.5 p-3 text-sm no-underline ${
                  selected ? "ring-2 ring-accent" : ""
                }`}
              >
                <span className="font-medium text-text-primary">
                  <bdi>{item.name}</bdi>
                </span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <bdi dir="ltr" className="font-mono">
                    {item.scorecard_key}
                  </bdi>
                  <span>{copy.listVersion(formatNumber(item.version, lang))}</span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
