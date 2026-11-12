import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { Button, Table } from "@heroui/react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { getLocalTimeZone } from "@internationalized/date";

import AuditEventDrawer from "@/components/admin/audit/AuditEventDrawer";
import AuditFilterBar from "@/components/admin/audit/AuditFilterBar";
import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_AUDIT_ERROR_CODES, ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import OrgNoAccess from "@/components/org/OrgNoAccess";
import { OrgPageHeader } from "@/components/org/OrgUi";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import { auditEventLabel, auditTargetLabel } from "@/lib/audit-labels";
import {
  auditQueryParams,
  hasAuditFilters,
  readAuditFilters,
  writeAuditFilters,
  type AuditFilters,
} from "@/lib/audit-filters";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useOrgAuditEventTypes, useOrgAuditEvents, useOrgMembers } from "@/lib/org-api";
import type { AuditEvent } from "@/types/organizations";

/** The audit error codes of both maps, org-wide first (HOM-243). */
const AUDIT_CODES = { ...ORG_ERROR_CODES, ...ORG_AUDIT_ERROR_CODES };

interface Paging {
  filterKey: string;
  /** One entry per page seen; the first page has no cursor. */
  cursors: Array<string | undefined>;
}

/**
 * The organization's audit log (HOM-243): the same filters, table, drawer and
 * cursor paging as the workspace audit page, fed by the organization read.
 */
export default function OrgAuditPage() {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const { organization } = useOrgAccess();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readAuditFilters(searchParams), [searchParams]);
  const timeZone = useMemo(() => getLocalTimeZone(), []);
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  // The cursors of the pages walked so far, kept next to the filters they
  // belong to: other filters describe a different list, where an old cursor
  // means nothing, so paging starts from the first page again.
  const filterKey = useMemo(() => writeAuditFilters(filters).toString(), [filters]);
  const [paging, setPaging] = useState<Paging>({ filterKey, cursors: [undefined] });
  const cursors = paging.filterKey === filterKey ? paging.cursors : [undefined];
  const cursor = cursors[cursors.length - 1];

  function goTo(next: Array<string | undefined>) {
    setPaging({ filterKey, cursors: next });
  }

  const params = auditQueryParams(filters, { cursor, timeZone });
  const auditQuery = useOrgAuditEvents(organization.id, params);
  const catalogQuery = useOrgAuditEventTypes(organization.id);
  const membersQuery = useOrgMembers(organization.id);
  const gone = useOrgGone(auditQuery.error);
  const actors = useMemo(
    () =>
      (membersQuery.data?.items ?? []).map((member) => ({
        user_id: member.user_id,
        label: member.display_name || member.email || member.user_id,
      })),
    [membersQuery.data],
  );
  const actorNames = useMemo(
    () => new Map(actors.map((actor) => [actor.user_id, actor.label])),
    [actors],
  );

  const errorCode = auditQuery.isError ? apiErrorInfo(auditQuery.error).code : null;
  const pageNumber = formatNumber(cursors.length, lang);
  const items = auditQuery.data?.items ?? [];

  function actorOf(event: AuditEvent): string {
    if (!event.actor_user_id) return copy.systemActor;
    return actorNames.get(event.actor_user_id) ?? event.actor_user_id;
  }

  function applyFilters(next: AuditFilters) {
    setSearchParams(writeAuditFilters(next, searchParams));
  }

  function firstPage() {
    goTo([undefined]);
  }

  function clearDateRange() {
    applyFilters({ ...filters, from: "", to: "" });
  }

  if (gone) return <OrgNoAccess />;

  return (
    <div className="platform-page">
      <OrgPageHeader eyebrow={organization.name} name="eyebrow" title={copy.auditTitle} />
      <p className="mt-0 mb-4 text-sm text-muted">{copy.auditIntro}</p>

      <AuditFilterBar
        filters={filters}
        actors={actors}
        onChange={applyFilters}
        catalog={{
          isPending: catalogQuery.isPending,
          isError: catalogQuery.isError,
          items: catalogQuery.data?.items,
        }}
      />

      <div className="mt-4">
        {auditQuery.isPending ? (
          <LoadingState label={copy.loadingAudit} />
        ) : auditQuery.isError ? (
          <div className="grid gap-3">
            <ErrorState
              error={auditQuery.error}
              codes={AUDIT_CODES}
              fallback={copy.auditFailed}
            />
            {errorCode === "invalid_cursor" && (
              <Button variant="secondary" size="sm" className="justify-self-start" onPress={firstPage}>
                {copy.auditFirstPage}
              </Button>
            )}
            {errorCode === "invalid_audit_range" && (
              <Button variant="secondary" size="sm" className="justify-self-start" onPress={clearDateRange}>
                {copy.clearRange}
              </Button>
            )}
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            label={hasAuditFilters(filters) ? copy.noAuditEventsFiltered : copy.noAuditEvents}
          />
        ) : (
          <>
            <Table aria-busy={auditQuery.isFetching}>
              <Table.ScrollContainer>
                <Table.Content aria-label={copy.auditTableLabel}>
                  <Table.Header>
                    <Table.Column isRowHeader>{copy.columnTime}</Table.Column>
                    <Table.Column>{copy.columnEvent}</Table.Column>
                    <Table.Column>{copy.columnActor}</Table.Column>
                    <Table.Column>{copy.columnTarget}</Table.Column>
                    <Table.Column>{copy.columnOutcome}</Table.Column>
                  </Table.Header>
                  <Table.Body>
                    {items.map((event) => (
                      <Table.Row key={event.id} id={event.id} onAction={() => setSelected(event)}>
                        <Table.Cell>
                          <time dateTime={event.occurred_at}>{formatDate(event.occurred_at, lang)}</time>
                        </Table.Cell>
                        <Table.Cell>
                          {/* An unknown type is shown as sent, never guessed. */}
                          {auditEventLabel(event.event_type, lang) ?? (
                            <bdi dir="ltr">{event.event_type}</bdi>
                          )}
                        </Table.Cell>
                        <Table.Cell><bdi dir="auto">{actorOf(event)}</bdi></Table.Cell>
                        <Table.Cell>
                          {event.target_type
                            ? auditTargetLabel(event.target_type, lang)
                            : copy.noTarget}
                        </Table.Cell>
                        <Table.Cell><StatusBadge status={event.outcome} /></Table.Cell>
                      </Table.Row>
                    ))}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>

            <nav
              className="mt-3 flex items-center justify-between gap-3"
              aria-label={copy.auditTableLabel}
            >
              <Button
                variant="secondary"
                size="sm"
                // Still focusable while a page loads, so focus is never dropped to the page.
                aria-disabled={cursors.length === 1 || auditQuery.isFetching}
                onPress={() => {
                  if (cursors.length === 1 || auditQuery.isFetching) return;
                  goTo(cursors.slice(0, -1));
                }}
              >
                <ChevronLeft size={15} aria-hidden="true" className="rtl-mirror" />
                {copy.previous}
              </Button>
              <span className="text-xs text-muted" aria-live="polite">
                {copy.auditPage(pageNumber)}
              </span>
              <Button
                variant="secondary"
                size="sm"
                aria-disabled={!auditQuery.data?.next_cursor || auditQuery.isFetching}
                onPress={() => {
                  const next = auditQuery.data?.next_cursor;
                  if (!next || auditQuery.isFetching) return;
                  goTo([...cursors, next]);
                }}
              >
                {copy.next}
                <ChevronRight size={15} aria-hidden="true" className="rtl-mirror" />
              </Button>
            </nav>
          </>
        )}
      </div>

      <AuditEventDrawer
        event={selected}
        actorName={selected ? actorOf(selected) : ""}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
