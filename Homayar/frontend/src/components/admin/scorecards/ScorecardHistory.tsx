import { useId } from "react";
import { Button, Chip, Table } from "@heroui/react";

import { SCORECARDS_COPY } from "@/components/admin/scorecards/scorecards-copy";
import { ErrorState, LoadingState } from "@/components/platform/Ui";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { ScorecardVersionItem } from "@/types/scorecards";

/**
 * Every stored version of one scorecard, newest first. The first row is the
 * current version; the others open read only through `?version=`.
 */
export default function ScorecardHistory({
  items,
  memberNames,
  isLoading,
  error,
  onRetry,
  onView,
}: {
  items: ScorecardVersionItem[];
  memberNames: Map<string, string>;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  onView: (version: number) => void;
}) {
  const { lang } = useI18n();
  const copy = SCORECARDS_COPY[lang];
  const headingId = useId();
  const latest = items[0]?.version ?? null;

  return (
    <section className="mt-6" aria-labelledby={headingId}>
      <h2 id={headingId} className="m-0 mb-3 text-sm font-semibold text-text-primary">
        {copy.history}
      </h2>
      {error !== null ? (
        <div className="grid gap-3">
          <ErrorState error={error} fallback={copy.historyError} />
          <Button variant="secondary" size="sm" className="justify-self-start" onPress={onRetry}>
            {copy.tryAgain}
          </Button>
        </div>
      ) : isLoading ? (
        <LoadingState label={copy.loading} />
      ) : (
        <Table>
          <Table.ScrollContainer>
            <Table.Content aria-label={copy.history}>
              <Table.Header>
                <Table.Column isRowHeader>{copy.columns.version}</Table.Column>
                <Table.Column>{copy.columns.name}</Table.Column>
                <Table.Column>{copy.columns.reason}</Table.Column>
                <Table.Column>{copy.columns.publishedBy}</Table.Column>
                <Table.Column>{copy.columns.date}</Table.Column>
                {/* Holds the current mark or the view button; no heading of its own. */}
                <Table.Column aria-label={copy.actions}>{""}</Table.Column>
              </Table.Header>
              <Table.Body>
                {items.map((item) => {
                  const version = formatNumber(item.version, lang);
                  return (
                    <Table.Row key={item.version} id={String(item.version)}>
                      <Table.Cell>{version}</Table.Cell>
                      <Table.Cell>
                        <bdi>{item.name}</bdi>
                      </Table.Cell>
                      <Table.Cell>
                        <bdi>{item.reason}</bdi>
                      </Table.Cell>
                      <Table.Cell>
                        {item.created_by_user_id && memberNames.has(item.created_by_user_id) ? (
                          <bdi>{memberNames.get(item.created_by_user_id)}</bdi>
                        ) : (
                          "—"
                        )}
                      </Table.Cell>
                      <Table.Cell>
                        <time dateTime={item.created_at}>{formatDate(item.created_at, lang)}</time>
                      </Table.Cell>
                      <Table.Cell>
                        {item.version === latest ? (
                          <Chip size="sm" variant="soft" color="accent">
                            <Chip.Label>{copy.current}</Chip.Label>
                          </Chip>
                        ) : (
                          <Button variant="secondary" size="sm" onPress={() => onView(item.version)}>
                            {copy.viewVersion(version)}
                          </Button>
                        )}
                      </Table.Cell>
                    </Table.Row>
                  );
                })}
              </Table.Body>
            </Table.Content>
          </Table.ScrollContainer>
        </Table>
      )}
    </section>
  );
}
