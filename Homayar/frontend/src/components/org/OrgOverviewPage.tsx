import { useId, useState } from "react";
import { Card, Description, Label, ListBox, Select, Table } from "@heroui/react";

import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_COPY, type OrgCopy } from "@/components/org/org-copy";
import {
  ORG_WEEKS,
  isReleased,
  orgTotals,
  sinceForWeeks,
  statusCount,
  windowDays,
  type OrgWeeks,
} from "@/components/org/org-model";
import OrgNoAccess from "@/components/org/OrgNoAccess";
import { NamedText, OrgPageHeader, OrgReadError, OrgRoleChip } from "@/components/org/OrgUi";
import { EmptyState, LoadingState } from "@/components/platform/Ui";
import { formatAudioHours, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useOrgAggregates } from "@/lib/org-api";
import type { WorkspaceAggregate } from "@/types/organizations";

const WEEKS_LABEL: Record<OrgWeeks, (copy: OrgCopy) => string> = {
  "4": (copy) => copy.weeks4,
  "13": (copy) => copy.weeks13,
  "26": (copy) => copy.weeks26,
  "52": (copy) => copy.weeks52,
};

/** Why a workspace is hidden, as a `{name}` template for `NamedText`. */
function hiddenReason(item: WorkspaceAggregate, minCohortAgents: number, lang: "en" | "fa", copy: OrgCopy) {
  if (item.disclosure.reason === "small_group") return copy.hiddenSmallGroup(formatNumber(minCohortAgents, lang));
  if (item.disclosure.reason === "complementary_disclosure") return copy.hiddenComplementary;
  return copy.hiddenOther;
}

/**
 * Meetings per workspace as plain bars; the longest bar is the largest
 * released count. A hidden workspace draws no bar and shows no number.
 */
function MeetingsChart({ items }: { items: WorkspaceAggregate[] }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const headingId = useId();
  const largest = Math.max(0, ...items.filter(isReleased).map((item) => item.meetings_total));
  const rows = items
    .map((item) =>
      isReleased(item)
        ? `${item.workspace_name}: ${formatNumber(item.meetings_total, lang)}`
        : copy.chartHiddenRow(item.workspace_name),
    )
    .join(lang === "fa" ? "، " : ", ");

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h2 id={headingId} className="m-0 text-base font-semibold text-foreground">
        {copy.chartTitle}
      </h2>
      {/* One picture for screen readers: the label lists every bar and its count. */}
      <div role="img" aria-label={copy.chartLabel(rows)} className="flex flex-col gap-2">
        {items.map((item) => {
          const released = isReleased(item);
          const share = released && largest > 0 ? (item.meetings_total / largest) * 100 : 0;
          return (
            <div key={item.workspace_id} className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] items-center gap-3">
              <span dir="auto" className="truncate text-sm text-foreground">
                {item.workspace_name}
              </span>
              <div className="h-3 overflow-hidden rounded-full bg-default">
                {released && <div className="h-full rounded-full bg-accent" style={{ width: `${share}%` }} />}
              </div>
              <span className={released ? "text-sm font-medium tabular-nums text-foreground" : "text-sm text-muted"}>
                {released ? formatNumber(item.meetings_total, lang) : copy.hidden}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function TotalsTable({ items }: { items: WorkspaceAggregate[] }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  return (
    <Table className="min-w-0">
      <Table.ScrollContainer
        role="region"
        tabIndex={0}
        aria-label={copy.tableScrolls(copy.totalsLabel)}
        className="platform-members-table-scroll"
      >
        <Table.Content aria-label={copy.totalsLabel}>
          <Table.Header>
            <Table.Column id="workspace" isRowHeader>
              {copy.columnWorkspace}
            </Table.Column>
            <Table.Column id="meetings">{copy.columnMeetings}</Table.Column>
            <Table.Column id="completed">{copy.columnCompleted}</Table.Column>
            <Table.Column id="failed">{copy.columnFailed}</Table.Column>
            <Table.Column id="audio">{copy.columnAudio}</Table.Column>
            <Table.Column id="actions">{copy.columnActionItems}</Table.Column>
          </Table.Header>
          <Table.Body items={items.map((item) => ({ ...item, id: item.workspace_id }))} dependencies={[lang]}>
            {(item) => {
              const counts = isReleased(item)
                ? [
                    formatNumber(item.meetings_total, lang),
                    formatNumber(statusCount(item, "completed"), lang),
                    formatNumber(statusCount(item, "failed"), lang),
                    formatAudioHours(item.audio_seconds_total, lang),
                    formatNumber(item.action_items_total, lang),
                  ]
                : Array<string>(5).fill(copy.hidden);
              return (
                <Table.Row id={item.workspace_id}>
                  <Table.Cell>
                    <span dir="auto" className="font-semibold text-foreground">
                      {item.workspace_name}
                    </span>
                  </Table.Cell>
                  {counts.map((count, index) => (
                    <Table.Cell key={index}>{count}</Table.Cell>
                  ))}
                </Table.Row>
              );
            }}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
  );
}

/** One line per hidden workspace, with the reason the server gave. */
function HiddenReasons({ items, minCohortAgents }: { items: WorkspaceAggregate[]; minCohortAgents: number }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const hidden = items.filter((item) => !isReleased(item));
  if (hidden.length === 0) return null;
  return (
    <ul className="m-0 list-none space-y-1 p-0 text-sm text-muted">
      {hidden.map((item) => (
        <li key={item.workspace_id}>
          <NamedText template={hiddenReason(item, minCohortAgents, lang, copy)} names={{ name: item.workspace_name }} />
        </li>
      ))}
    </ul>
  );
}

/**
 * The organization overview (HOM-193): totals over every workspace the role
 * may see, a bar per workspace and the same numbers as a table. Since
 * contract 1.7.0 (HOM-320) a small workspace can be withheld: its counts are
 * null, so it shows "Hidden" and the sums leave it out (HOM-325).
 */
export default function OrgOverviewPage() {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const { organization, role } = useOrgAccess();
  // `since` is fixed when the period is chosen, so the query key stays stable.
  const [period, setPeriod] = useState<{ value: OrgWeeks; since: string | null }>({ value: "4", since: null });
  const aggregatesQuery = useOrgAggregates(organization.id, period.since);
  const gone = useOrgGone(aggregatesQuery.error);

  if (gone) return <OrgNoAccess />;

  const data = aggregatesQuery.data;
  const items = data?.items ?? [];
  const totals = orgTotals(items);
  // With every workspace hidden, a sum of 0 would be false: there is nothing to sum.
  const allHidden = totals.workspaces > 0 && totals.hidden === totals.workspaces;
  const cards = [
    { label: copy.cardWorkspaces, value: formatNumber(totals.workspaces, lang) },
    { label: copy.cardMeetings, value: allHidden ? copy.hidden : formatNumber(totals.meetings, lang) },
    { label: copy.cardAudio, value: allHidden ? copy.hidden : formatAudioHours(totals.audioSeconds, lang) },
    { label: copy.cardActionItems, value: allHidden ? copy.hidden : formatNumber(totals.actionItems, lang) },
  ];
  const shown = data ? windowDays(data.coverage_window, lang) : null;

  return (
    <div className="platform-page space-y-5">
      <OrgPageHeader eyebrow={copy.organization} title={organization.name} name="title" badge={<OrgRoleChip role={role} />} />

      <Select
        // Layout containment, as in Header.tsx, for the hidden native <select> in right to left.
        className="w-full [contain:layout] sm:w-80"
        value={period.value}
        onChange={(key) => {
          if (typeof key !== "string" || !ORG_WEEKS.includes(key as OrgWeeks)) return;
          const value = key as OrgWeeks;
          setPeriod({ value, since: sinceForWeeks(value) });
        }}
      >
        <Label>{copy.period}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.Indicator />
        </Select.Trigger>
        <Description>{copy.periodHelp}</Description>
        <Select.Popover>
          <ListBox>
            {ORG_WEEKS.map((value) => (
              <ListBox.Item key={value} id={value} textValue={WEEKS_LABEL[value](copy)}>
                {WEEKS_LABEL[value](copy)}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

      {aggregatesQuery.isPending ? (
        <LoadingState label={copy.loadingTotals} />
      ) : aggregatesQuery.isError ? (
        <OrgReadError error={aggregatesQuery.error} fallback={copy.totalsFailed} onRetry={() => void aggregatesQuery.refetch()} />
      ) : (
        <>
          {/* The window the server really used; a refused `since` falls back to the default. */}
          {shown && <p className="m-0 text-sm text-muted">{copy.showingWindow(shown.from, shown.last)}</p>}

          <dl className="m-0 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {cards.map((card) => (
              <Card key={card.label} className="gap-1">
                <dt className="text-sm text-muted">{card.label}</dt>
                <dd className="m-0 text-2xl font-semibold text-foreground">{card.value}</dd>
              </Card>
            ))}
          </dl>
          {totals.hidden > 0 && (
            <p className="m-0 text-sm text-muted">{copy.totalsLeaveOut(totals.hidden, formatNumber(totals.hidden, lang))}</p>
          )}

          {items.length === 0 ? (
            <EmptyState label={organization.unrestricted ? copy.noData : copy.noGrant} />
          ) : (
            <Card<"section"> render={(props) => <section {...props} />} className="min-w-0">
              <Card.Content className="min-w-0 space-y-6">
                <MeetingsChart items={items} />
                <TotalsTable items={items} />
                <HiddenReasons items={items} minCohortAgents={data?.disclosure_policy.min_cohort_agents ?? 0} />
              </Card.Content>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
