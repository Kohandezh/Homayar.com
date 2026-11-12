import { useMemo } from "react";
import { Link as RouterLink, useSearchParams } from "react-router";
import type { Key } from "react";
import { Button, Card, Chip, Label, ListBox, Select, Switch, Table, linkVariants } from "@heroui/react";

import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { useAuth } from "@/lib/auth-context";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMembers, useTeams } from "@/lib/platform-api";
import { useReviewItems } from "@/lib/review-api";
import { REVIEW_ERROR_CODES, REVIEWS_COPY, type ReviewsCopy } from "@/components/reviews/reviews-copy";
import type { ReviewItem, ReviewItemListParams, ReviewItemStatus } from "@/types/reviews";

/** The URL values of the status filter; the option `active` means no status param. */
const STATUS_OPTIONS = ["active", "open", "in_review", "completed", "cancelled"] as const;
type StatusOption = (typeof STATUS_OPTIONS)[number];

const STATUS_QUERY: Record<Exclude<StatusOption, "active">, ReviewItemStatus> = {
  open: "open",
  in_review: "in_review",
  completed: "completed",
  cancelled: "cancelled",
};

const ASSIGNEE_OPTIONS = ["anyone", "me", "unassigned"] as const;
type AssigneeOption = (typeof ASSIGNEE_OPTIONS)[number];

function statusOptionOf(value: string | null): StatusOption {
  return STATUS_OPTIONS.includes(value as StatusOption) ? (value as StatusOption) : "active";
}

function assigneeOptionOf(value: string | null): AssigneeOption {
  return ASSIGNEE_OPTIONS.includes(value as AssigneeOption) ? (value as AssigneeOption) : "anyone";
}

const STATUS_CHIP_COLORS: Record<string, "default" | "accent" | "success" | "danger"> = {
  open: "default",
  in_review: "accent",
  completed: "success",
  cancelled: "danger",
};

function statusOptionLabel(option: StatusOption, copy: ReviewsCopy) {
  if (option === "active") return copy.filterActive;
  return copy.statuses[option];
}

function assigneeOptionLabel(option: AssigneeOption, copy: ReviewsCopy) {
  if (option === "me") return copy.filterMe;
  if (option === "unassigned") return copy.filterUnassigned;
  return copy.filterAnyone;
}

function statusText(status: string, copy: ReviewsCopy) {
  if (status === "open") return copy.statuses.open;
  if (status === "in_review") return copy.statuses.in_review;
  if (status === "completed") return copy.statuses.completed;
  if (status === "cancelled") return copy.statuses.cancelled;
  return status;
}

function sourceText(source: string, copy: ReviewsCopy) {
  return source === "manual" ? copy.sources.manual : source === "sample" ? copy.sources.sample : source;
}

/**
 * The queue list page (`/reviews`, HOM-265 AC2). The filters live in the URL,
 * so a filtered view can be shared and reloaded. The list is never filtered
 * by team on the client: the API itself decides which items the caller sees.
 */
export default function ReviewQueuePage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const copy = REVIEWS_COPY[lang];
  const [searchParams, setSearchParams] = useSearchParams();

  const statusOption = statusOptionOf(searchParams.get("status"));
  const assigneeOption = assigneeOptionOf(searchParams.get("assignee"));
  const overdueOnly = searchParams.get("overdue") === "true";

  const params: ReviewItemListParams = {
    ...(statusOption !== "active" ? { status: STATUS_QUERY[statusOption] } : {}),
    ...(assigneeOption !== "anyone" ? { assignee: assigneeOption } : {}),
    ...(overdueOnly ? { overdue: true } : {}),
  };
  const listQuery = useReviewItems(params);

  // Names for the rows, from the member and team lists the console already
  // keeps. A failed read degrades to the raw id, never to a guess: the id is
  // shown only when no name is known.
  const membersQuery = useMembers();
  const teamsQuery = useTeams();
  const memberNames = useMemo(
    () => new Map((membersQuery.data?.items ?? []).map((member) => [member.user_id, member.display_name])),
    [membersQuery.data],
  );
  const teamNames = useMemo(
    () => new Map((teamsQuery.data?.items ?? []).map((team) => [team.id, team.name])),
    [teamsQuery.data],
  );

  const items = listQuery.data?.pages.flatMap((page) => page.items) ?? [];

  function setParam(name: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null) next.delete(name);
    else next.set(name, value);
    setSearchParams(next, { replace: true });
  }

  const tableLabel = copy.listLabel;

  return (
    <div className="platform-page space-y-5">
      <PageHeader eyebrow={workspace?.name ?? undefined} title={copy.title} />
      <p className="m-0 text-sm text-muted">{copy.intro}</p>

      <div className="flex flex-wrap items-end gap-3">
        <Select
          className="w-full sm:w-44"
          value={statusOption}
          onChange={(key: Key | null) =>
            setParam("status", typeof key === "string" && key !== "active" ? key : null)
          }
          variant="secondary"
        >
          <Label>{copy.filterStatus}</Label>
          <Select.Trigger>
            <Select.Value />
            <Select.Indicator />
          </Select.Trigger>
          <Select.Popover>
            <ListBox>
              {STATUS_OPTIONS.map((option) => (
                <ListBox.Item
                  key={option}
                  id={option}
                  textValue={statusOptionLabel(option, copy)}
                >
                  {statusOptionLabel(option, copy)}
                  <ListBox.ItemIndicator />
                </ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
        </Select>

        <Select
          className="w-full sm:w-44"
          value={assigneeOption}
          onChange={(key: Key | null) =>
            setParam("assignee", typeof key === "string" && key !== "anyone" ? key : null)
          }
          variant="secondary"
        >
          <Label>{copy.filterAssignee}</Label>
          <Select.Trigger>
            <Select.Value />
            <Select.Indicator />
          </Select.Trigger>
          <Select.Popover>
            <ListBox>
              {ASSIGNEE_OPTIONS.map((option) => (
                <ListBox.Item key={option} id={option} textValue={assigneeOptionLabel(option, copy)}>
                  {assigneeOptionLabel(option, copy)}
                  <ListBox.ItemIndicator />
                </ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
        </Select>

        <Switch isSelected={overdueOnly} onChange={(selected) => setParam("overdue", selected ? "true" : null)}>
          <Switch.Content>
            <Switch.Control>
              <Switch.Thumb />
            </Switch.Control>
            {copy.filterOverdue}
          </Switch.Content>
        </Switch>
      </div>

      <Card<"section"> render={(props) => <section {...props} />} className="min-w-0">
        <Card.Content className="min-w-0" aria-busy={listQuery.isPending || undefined}>
          {listQuery.isPending ? (
            <LoadingState label={copy.loading} />
          ) : listQuery.isError ? (
            <div className="grid gap-3">
              <ErrorState error={listQuery.error} codes={REVIEW_ERROR_CODES} fallback={copy.loadError} />
              <div>
                <Button variant="secondary" size="sm" onPress={() => void listQuery.refetch()}>
                  {copy.tryAgain}
                </Button>
              </div>
            </div>
          ) : items.length === 0 ? (
            <div className="space-y-2">
              <EmptyState label={copy.empty} />
              <p className="m-0 text-center text-sm text-muted">{copy.emptyScope}</p>
            </div>
          ) : (
            <>
              <Table className="min-w-0">
                <Table.ScrollContainer
                  role="region"
                  tabIndex={0}
                  aria-label={tableLabel}
                  className="platform-members-table-scroll"
                >
                  <Table.Content aria-label={tableLabel}>
                    <Table.Header>
                      <Table.Column id="status" isRowHeader>
                        {copy.columnStatus}
                      </Table.Column>
                      <Table.Column id="due">{copy.columnDue}</Table.Column>
                      <Table.Column id="assignee">{copy.columnAssignee}</Table.Column>
                      <Table.Column id="team">{copy.columnTeam}</Table.Column>
                      <Table.Column id="source">{copy.columnSource}</Table.Column>
                      <Table.Column id="meeting">{copy.columnMeeting}</Table.Column>
                      <Table.Column id="actions">{copy.columnActions}</Table.Column>
                    </Table.Header>
                    <Table.Body items={items} dependencies={[lang, memberNames, teamNames]}>
                      {(item: ReviewItem) => (
                        <Table.Row id={item.id}>
                          <Table.Cell>
                            <Chip color={STATUS_CHIP_COLORS[item.status] ?? "default"} variant="soft" size="sm">
                              <Chip.Label>{statusText(item.status, copy)}</Chip.Label>
                            </Chip>
                          </Table.Cell>
                          <Table.Cell>
                            <span className="flex flex-wrap items-center gap-1.5 whitespace-nowrap">
                              {formatDate(item.due_at, lang)}
                              {item.is_overdue && (
                                <Chip color="danger" variant="soft" size="sm">
                                  <Chip.Label>{copy.overdue}</Chip.Label>
                                </Chip>
                              )}
                            </span>
                          </Table.Cell>
                          <Table.Cell>
                            <span dir="auto">
                              {item.assignee_user_id === null
                                ? copy.unassigned
                                : (memberNames.get(item.assignee_user_id) ?? item.assignee_user_id)}
                            </span>
                          </Table.Cell>
                          <Table.Cell>
                            <span dir="auto">
                              {item.team_id === null ? copy.noTeam : (teamNames.get(item.team_id) ?? item.team_id)}
                            </span>
                          </Table.Cell>
                          <Table.Cell>
                            <span className="whitespace-nowrap">{sourceText(item.source, copy)}</span>
                          </Table.Cell>
                          <Table.Cell>
                            <RouterLink to={`/meetings/${item.meeting_id}`} className={linkVariants().base()}>
                              {copy.openReport}
                            </RouterLink>
                          </Table.Cell>
                          <Table.Cell>
                            <RouterLink
                              to={`/reviews/${item.id}`}
                              aria-label={copy.openItem}
                              className={linkVariants().base()}
                            >
                              {copy.view}
                            </RouterLink>
                          </Table.Cell>
                        </Table.Row>
                      )}
                    </Table.Body>
                  </Table.Content>
                </Table.ScrollContainer>
              </Table>
              {listQuery.hasNextPage && (
                <div className="mt-3 flex justify-center">
                  <Button
                    variant="secondary"
                    size="sm"
                    isPending={listQuery.isFetchingNextPage}
                    onPress={() => void listQuery.fetchNextPage()}
                  >
                    {copy.loadMore}
                  </Button>
                </div>
              )}
            </>
          )}
        </Card.Content>
      </Card>
    </div>
  );
}
