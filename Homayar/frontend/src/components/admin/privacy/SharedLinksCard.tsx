import { useId, useMemo, useState } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Chip, Table, ToggleButton, ToggleButtonGroup } from "@heroui/react";

import OverviewCard from "@/components/admin/overview/OverviewCard";
import { overviewLinkClass, queryCardState, type CardState } from "@/components/admin/overview/card-state";
import { labelOf, PRIVACY_COPY, SHARE_ERROR_CODES } from "@/components/admin/privacy/privacy-copy";
import RevokeShareDialog from "@/components/admin/privacy/RevokeShareDialog";
import { apiErrorInfo } from "@/lib/api-errors";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMembers } from "@/lib/platform-api";
import { privacyQueryKeys, useAdminShares, type ShareFilter } from "@/lib/privacy-api";
import type { AdminShareItem } from "@/types/privacy";

const FILTERS: readonly ShareFilter[] = ["active", "expired", "revoked", "all"];

/**
 * The register of every share link of the workspace (HOM-259): what is
 * shared outside right now, filterable by status, paged with a cursor. The
 * share token never reaches this card; the backend's item has none and no
 * extra field is rendered. A new filter is a new list from page one.
 */
export default function SharedLinksCard() {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<ShareFilter>("active");
  const [revoking, setRevoking] = useState<AdminShareItem | null>(null);
  // The filter whose list restarted from page one because a next page's
  // cursor was stale. Its notice stays until the owner loads more or
  // chooses a filter again.
  const [restartedFilter, setRestartedFilter] = useState<ShareFilter | null>(null);
  const listChanged = restartedFilter === filter;
  const status = filter === "all" ? undefined : filter;
  const query = useAdminShares(status);
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
  const nextPageStale =
    query.isError && query.isFetchNextPageError && apiErrorInfo(query.error).code === "invalid_cursor";
  // A current failure always shows its own text; the notice only fills
  // the footer when nothing else went wrong since the restart.
  const footerError =
    query.data && query.isError
      ? query.isFetchNextPageError
        ? nextPageStale
          ? copy.shareListChanged
          : copy.sharesLoadMoreFailed
        : copy.sharesFailed
      : listChanged
        ? copy.shareListChanged
        : null;
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

  function loadMore() {
    const pressedFilter = filter;
    setRestartedFilter(null);
    void query.fetchNextPage().then((result) => {
      if (!result.isFetchNextPageError || apiErrorInfo(result.error).code !== "invalid_cursor") return;
      // A stale cursor means the pages behind it are gone: restart this
      // filter's list from page one, so the next request carries no cursor.
      // The notice is set here, in the press handler, so a slow re-read
      // cannot lose it.
      setRestartedFilter(pressedFilter);
      void queryClient.resetQueries({ queryKey: privacyQueryKeys.shares(status) });
    });
  }

  return (
    <OverviewCard
      title={copy.sharesHeading}
      action={
        <ToggleButtonGroup
          aria-label={copy.sharesFilterLabel}
          selectionMode="single"
          disallowEmptySelection
          size="sm"
          selectedKeys={[filter]}
          onSelectionChange={(keys) => {
            const [next] = [...keys] as ShareFilter[];
            if (!next || next === filter) return;
            setRestartedFilter(null);
            // The chosen filter starts a fresh list from page one, never its
            // cached pages.
            void queryClient.resetQueries({
              queryKey: privacyQueryKeys.shares(next === "all" ? undefined : next),
            });
            setFilter(next);
          }}
        >
          {FILTERS.map((value, index) => (
            <ToggleButton key={value} id={value}>
              {index > 0 && <ToggleButtonGroup.Separator />}
              {copy.shareFilters[value]}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      }
      state={state}
      error={query.error}
      errorCodes={SHARE_ERROR_CODES}
      emptyText={copy.emptyShares[filter]}
      footer={
        query.hasNextPage || footerError ? (
          <div className="flex flex-wrap items-center gap-3">
            {query.hasNextPage && (
              <Button
                variant="secondary"
                size="sm"
                isDisabled={query.isFetchingNextPage}
                onPress={loadMore}
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
          <Table.Content aria-label={copy.sharesTable} className="min-w-[860px]">
            <Table.Header>
              <Table.Column isRowHeader>{copy.shareColumns.meeting}</Table.Column>
              <Table.Column>{copy.shareColumns.kind}</Table.Column>
              <Table.Column>{copy.shareColumns.status}</Table.Column>
              <Table.Column>{copy.shareColumns.createdBy}</Table.Column>
              <Table.Column>{copy.shareColumns.createdAt}</Table.Column>
              <Table.Column>{copy.shareColumns.expiresAt}</Table.Column>
              <Table.Column>{copy.shareColumns.opened}</Table.Column>
              <Table.Column aria-label={copy.shareColumns.actions}>{""}</Table.Column>
            </Table.Header>
            <Table.Body>
              {items.map((item) => {
                const titleId = `${rowIdPrefix}-${item.id}`;
                return (
                  <Table.Row key={item.id} id={item.id}>
                    <Table.Cell>
                      <Link className={overviewLinkClass} to={`/meetings/${encodeURIComponent(item.meeting_id)}`}>
                        <bdi id={titleId}>{item.meeting_title || copy.untitledConversation}</bdi>
                      </Link>
                    </Table.Cell>
                    <Table.Cell>{labelOf(copy.audiences, item.audience)}</Table.Cell>
                    <Table.Cell>
                      <Chip
                        size="sm"
                        variant="soft"
                        color={
                          item.status === "active" ? "success" : item.status === "revoked" ? "danger" : "default"
                        }
                      >
                        <Chip.Label>{labelOf(copy.shareStatuses, item.status)}</Chip.Label>
                      </Chip>
                    </Table.Cell>
                    <Table.Cell>
                      <bdi>{memberNames.get(item.created_by_user_id) || copy.unknownMember}</bdi>
                    </Table.Cell>
                    <Table.Cell>
                      <time dateTime={item.created_at}>{formatDate(item.created_at, lang)}</time>
                    </Table.Cell>
                    <Table.Cell>
                      {item.expires_at ? (
                        <time dateTime={item.expires_at}>{formatDate(item.expires_at, lang)}</time>
                      ) : (
                        copy.neverExpires
                      )}
                    </Table.Cell>
                    <Table.Cell>{formatNumber(item.access_count, lang)}</Table.Cell>
                    <Table.Cell>
                      {/* Only a live link can be revoked; the row's conversation describes it. */}
                      {item.status === "active" && (
                        <Button
                          variant="secondary"
                          size="sm"
                          aria-describedby={titleId}
                          onPress={() => setRevoking(item)}
                        >
                          {copy.revoke}
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
      {revoking && <RevokeShareDialog target={revoking} onClose={() => setRevoking(null)} />}
    </OverviewCard>
  );
}
