import { useId, useMemo, useState } from "react";
import { Link } from "react-router";
import { Button, Chip, Table, ToggleButton, ToggleButtonGroup } from "@heroui/react";
import { Unlock } from "lucide-react";

import OverviewCard from "@/components/admin/overview/OverviewCard";
import { overviewLinkClass, queryCardState, type CardState } from "@/components/admin/overview/card-state";
import { HOLD_ERROR_CODES, labelOf, PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import { formatReviewDay, isReviewDue } from "@/components/admin/privacy/privacy-model";
import ReleaseHoldDialog, { type ReleaseTarget } from "@/components/admin/privacy/ReleaseHoldDialog";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMembers } from "@/lib/platform-api";
import { dueBeforeFor, useLegalHolds, type LegalHoldFilter } from "@/lib/privacy-api";
import type { LegalHoldItem } from "@/types/privacy";

const FILTERS: readonly LegalHoldFilter[] = ["all", "review_due", "due_30_days"];

/**
 * The workspace's legal holds, review date first (HOM-192). The filter's
 * `due_before` is fixed when the filter is chosen, so the query key stays
 * the same while the owner pages; a new filter is a new list from page one.
 */
export default function LegalHoldRegisterCard() {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  const [filter, setFilter] = useState<{ value: LegalHoldFilter; dueBefore: string | undefined }>({
    value: "all",
    dueBefore: undefined,
  });
  const [releasing, setReleasing] = useState<ReleaseTarget | null>(null);
  const query = useLegalHolds(filter.dueBefore);
  const membersQuery = useMembers();
  const rowIdPrefix = useId();

  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  // Only a list with no data at all becomes the error state: a failed
  // "Load more" or background refetch keeps the rows on screen.
  const state: CardState = query.data
    ? items.length === 0
      ? "empty"
      : "data"
    : queryCardState(query, () => items.length === 0);
  const footerError = !query.data || !query.isError
    ? null
    : query.isFetchNextPageError
      ? copy.loadMoreFailed
      : copy.holdsFailed;
  // A failed members read leaves the names unknown.
  const memberNames = useMemo(
    () =>
      new Map(
        (membersQuery.data?.items ?? [])
          .filter((member) => member.display_name)
          .map((member) => [member.user_id, member.display_name]),
      ),
    [membersQuery.data],
  );
  const now = new Date();
  const emptyText =
    filter.value === "review_due" ? copy.emptyReviewDue : filter.value === "due_30_days" ? copy.emptyDue30 : copy.emptyAll;

  function meetingCell(item: LegalHoldItem, titleId: string) {
    if (!item.meeting_id) {
      return (
        <span id={titleId} className="text-muted">
          {copy.conversationDeleted}
        </span>
      );
    }
    return (
      <Link className={overviewLinkClass} to={`/meetings/${encodeURIComponent(item.meeting_id)}`}>
        <bdi id={titleId}>{item.meeting_title || copy.untitledConversation}</bdi>
      </Link>
    );
  }

  return (
    <OverviewCard
      title={copy.holdsHeading}
      action={
        <ToggleButtonGroup
          aria-label={copy.filterLabel}
          selectionMode="single"
          disallowEmptySelection
          size="sm"
          selectedKeys={[filter.value]}
          onSelectionChange={(keys) => {
            const [next] = [...keys] as LegalHoldFilter[];
            if (!next || next === filter.value) return;
            setFilter({ value: next, dueBefore: dueBeforeFor(next) });
          }}
        >
          {FILTERS.map((value, index) => (
            <ToggleButton key={value} id={value}>
              {index > 0 && <ToggleButtonGroup.Separator />}
              {copy.filters[value]}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      }
      state={state}
      error={query.error}
      errorCodes={HOLD_ERROR_CODES}
      emptyText={emptyText}
      footer={
        query.hasNextPage || footerError ? (
          <div className="flex flex-wrap items-center gap-3">
            {query.hasNextPage && (
              <Button
                variant="secondary"
                size="sm"
                isDisabled={query.isFetchingNextPage}
                onPress={() => void query.fetchNextPage()}
              >
                {copy.loadMore}
              </Button>
            )}
            {footerError && (
              <p role="alert" className="m-0 text-xs text-danger">
                {footerError}
              </p>
            )}
          </div>
        ) : undefined
      }
    >
      <Table className="min-w-0">
        <Table.ScrollContainer>
          <Table.Content aria-label={copy.holdsTable} className="min-w-[860px]">
            <Table.Header>
              <Table.Column isRowHeader>{copy.columns.meeting}</Table.Column>
              <Table.Column>{copy.columns.kind}</Table.Column>
              <Table.Column>{copy.columns.reason}</Table.Column>
              <Table.Column>{copy.columns.placedBy}</Table.Column>
              <Table.Column>{copy.columns.placedAt}</Table.Column>
              <Table.Column>{copy.columns.reviewAt}</Table.Column>
              <Table.Column aria-label={copy.columns.actions}>{""}</Table.Column>
            </Table.Header>
            <Table.Body>
              {items.map((item) => {
                const titleId = `${rowIdPrefix}-${item.media_id}`;
                return (
                  <Table.Row key={item.media_id} id={item.media_id}>
                    <Table.Cell>{meetingCell(item, titleId)}</Table.Cell>
                    <Table.Cell>{labelOf(copy.kinds, item.kind)}</Table.Cell>
                    <Table.Cell>{item.reason ? <bdi>{item.reason}</bdi> : "—"}</Table.Cell>
                    <Table.Cell>
                      <bdi>
                        {(item.placed_by_user_id && memberNames.get(item.placed_by_user_id)) || copy.unknownMember}
                      </bdi>
                    </Table.Cell>
                    <Table.Cell>
                      <time dateTime={item.placed_at}>{formatDate(item.placed_at, lang)}</time>
                    </Table.Cell>
                    <Table.Cell>
                      <span className="flex flex-wrap items-center gap-2">
                        <time dateTime={item.review_at}>{formatReviewDay(item.review_at, lang)}</time>
                        {isReviewDue(item.review_at, now) && (
                          <Chip size="sm" variant="soft" color="warning">
                            <Chip.Label>{copy.reviewDue}</Chip.Label>
                          </Chip>
                        )}
                      </span>
                    </Table.Cell>
                    <Table.Cell>
                      <Button
                        variant="secondary"
                        size="sm"
                        // The name stays "Release"; the row's conversation describes it.
                        aria-describedby={titleId}
                        onPress={() => setReleasing({ mediaId: item.media_id, meetingId: item.meeting_id })}
                      >
                        <Unlock aria-hidden="true" size={15} />
                        {copy.release}
                      </Button>
                    </Table.Cell>
                  </Table.Row>
                );
              })}
            </Table.Body>
          </Table.Content>
        </Table.ScrollContainer>
      </Table>
      {releasing && <ReleaseHoldDialog target={releasing} onClose={() => setReleasing(null)} />}
    </OverviewCard>
  );
}
