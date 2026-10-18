import { useId, useState } from "react";
import { Card, Label, ListBox, Select, Table } from "@heroui/react";

import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_COPY, type OrgCopy } from "@/components/org/org-copy";
import { ORG_PERIODS, orgTotals, sinceFor, statusCount, type OrgPeriod } from "@/components/org/org-model";
import OrgNoAccess from "@/components/org/OrgNoAccess";
import { OrgPageHeader, OrgReadError, OrgRoleChip } from "@/components/org/OrgUi";
import { EmptyState, LoadingState } from "@/components/platform/Ui";
import { formatAudioHours, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useOrgAggregates } from "@/lib/org-api";
import type { WorkspaceAggregate } from "@/types/organizations";

const PERIOD_LABEL: Record<OrgPeriod, (copy: OrgCopy) => string> = {
  all: (copy) => copy.allTime,
  "7": (copy) => copy.last7,
  "30": (copy) => copy.last30,
  "90": (copy) => copy.last90,
};

/** Meetings per workspace as plain bars; the longest bar is the largest count. */
function MeetingsChart({ items }: { items: WorkspaceAggregate[] }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const headingId = useId();
  const largest = Math.max(0, ...items.map((item) => item.meetings_total));
  const rows = items
    .map((item) => `${item.workspace_name}: ${formatNumber(item.meetings_total, lang)}`)
    .join(lang === "fa" ? "، " : ", ");

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h2 id={headingId} className="m-0 text-base font-semibold text-foreground">
        {copy.chartTitle}
      </h2>
      {/* One picture for screen readers: the label lists every bar and its count. */}
      <div role="img" aria-label={copy.chartLabel(rows)} className="flex flex-col gap-2">
        {items.map((item) => {
          const share = largest > 0 ? (item.meetings_total / largest) * 100 : 0;
          return (
            <div key={item.workspace_id} className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] items-center gap-3">
              <span dir="auto" className="truncate text-sm text-foreground">
                {item.workspace_name}
              </span>
              <div className="h-3 overflow-hidden rounded-full bg-default">
                <div className="h-full rounded-full bg-accent" style={{ width: `${share}%` }} />
              </div>
              <span className="text-sm font-medium tabular-nums text-foreground">
                {formatNumber(item.meetings_total, lang)}
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
            {(item) => (
              <Table.Row id={item.workspace_id}>
                <Table.Cell>
                  <span dir="auto" className="font-semibold text-foreground">
                    {item.workspace_name}
                  </span>
                </Table.Cell>
                <Table.Cell>{formatNumber(item.meetings_total, lang)}</Table.Cell>
                <Table.Cell>{formatNumber(statusCount(item, "completed"), lang)}</Table.Cell>
                <Table.Cell>{formatNumber(statusCount(item, "failed"), lang)}</Table.Cell>
                <Table.Cell>{formatAudioHours(item.audio_seconds_total, lang)}</Table.Cell>
                <Table.Cell>{formatNumber(item.action_items_total, lang)}</Table.Cell>
              </Table.Row>
            )}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
  );
}

/**
 * The organization overview (HOM-193): totals over every workspace the role
 * may see, a bar per workspace and the same numbers as a table.
 */
export default function OrgOverviewPage() {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const { organization, role } = useOrgAccess();
  // `since` is fixed when the period is chosen, so the query key stays stable.
  const [period, setPeriod] = useState<{ value: OrgPeriod; since: string | null }>({ value: "all", since: null });
  const aggregatesQuery = useOrgAggregates(organization.id, period.since);
  const gone = useOrgGone(aggregatesQuery.error);

  if (gone) return <OrgNoAccess />;

  const items = aggregatesQuery.data?.items ?? [];
  const totals = orgTotals(items);
  const cards = [
    { label: copy.cardWorkspaces, value: formatNumber(totals.workspaces, lang) },
    { label: copy.cardMeetings, value: formatNumber(totals.meetings, lang) },
    { label: copy.cardAudio, value: formatAudioHours(totals.audioSeconds, lang) },
    { label: copy.cardActionItems, value: formatNumber(totals.actionItems, lang) },
  ];

  return (
    <div className="platform-page space-y-5">
      <OrgPageHeader eyebrow={copy.organization} title={organization.name} name="title" badge={<OrgRoleChip role={role} />} />

      <Select
        // Layout containment, as in Header.tsx, for the hidden native <select> in right to left.
        className="w-full [contain:layout] sm:w-64"
        value={period.value}
        onChange={(key) => {
          if (typeof key !== "string" || !ORG_PERIODS.includes(key as OrgPeriod)) return;
          const value = key as OrgPeriod;
          setPeriod({ value, since: sinceFor(value) });
        }}
      >
        <Label>{copy.period}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            {ORG_PERIODS.map((value) => (
              <ListBox.Item key={value} id={value} textValue={PERIOD_LABEL[value](copy)}>
                {PERIOD_LABEL[value](copy)}
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
          <dl className="m-0 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {cards.map((card) => (
              <Card key={card.label} className="gap-1">
                <dt className="text-sm text-muted">{card.label}</dt>
                <dd className="m-0 text-2xl font-semibold text-foreground">{card.value}</dd>
              </Card>
            ))}
          </dl>

          {items.length === 0 ? (
            <EmptyState label={organization.unrestricted ? copy.noData : copy.noGrant} />
          ) : (
            <Card<"section"> render={(props) => <section {...props} />} className="min-w-0">
              <Card.Content className="min-w-0 space-y-6">
                <MeetingsChart items={items} />
                <TotalsTable items={items} />
              </Card.Content>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
