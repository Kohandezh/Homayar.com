import { useMemo } from "react";
import { Table } from "@heroui/react";

import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_COPY } from "@/components/org/org-copy";
import OrgNoAccess from "@/components/org/OrgNoAccess";
import { DayText, OrgPageHeader, OrgReadError } from "@/components/org/OrgUi";
import { EmptyState, LoadingState, StatusBadge } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useOrgRecords, useOrgWorkspaces } from "@/lib/org-api";
import { formatDuration, formatNumber, languageName } from "@/lib/format";

/**
 * The organization's cross-workspace records (HOM-243): the meeting metadata
 * the records API returns. No row links into a meeting, because an
 * organization grant gives no workspace content access.
 */
export default function OrgRecordsPage() {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const { organization } = useOrgAccess();
  const recordsQuery = useOrgRecords(organization.id);
  const workspacesQuery = useOrgWorkspaces(organization.id);
  const gone = useOrgGone(recordsQuery.error);
  const records = useMemo(() => recordsQuery.data?.items ?? [], [recordsQuery.data]);
  const workspaceNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const workspace of workspacesQuery.data?.items ?? []) {
      names.set(workspace.id, workspace.name);
    }
    return names;
  }, [workspacesQuery.data]);

  if (gone) return <OrgNoAccess />;

  return (
    <div className="platform-page space-y-5">
      <OrgPageHeader eyebrow={organization.name} name="eyebrow" title={copy.recordsTitle} />
      <p className="mt-0 mb-0 text-sm text-muted">{copy.recordsIntro}</p>

      {!recordsQuery.isPending && !recordsQuery.isError && (
        <p className="m-0 text-sm text-muted" aria-live="polite">
          {copy.recordsTotal(
            recordsQuery.data?.total ?? records.length,
            formatNumber(recordsQuery.data?.total ?? records.length, lang),
          )}
        </p>
      )}

      {recordsQuery.isPending ? (
        <LoadingState label={copy.loadingRecords} />
      ) : recordsQuery.isError ? (
        <OrgReadError
          error={recordsQuery.error}
          fallback={copy.recordsFailed}
          onRetry={() => void recordsQuery.refetch()}
        />
      ) : records.length === 0 ? (
        <EmptyState label={copy.noRecords} />
      ) : (
        <>
          {recordsQuery.data?.truncated && (
            <p className="m-0 text-sm text-warning" role="status">
              {copy.recordsTruncated}
            </p>
          )}
          <Table aria-busy={recordsQuery.isFetching}>
            <Table.ScrollContainer
              role="region"
              tabIndex={0}
              aria-label={copy.tableScrolls(copy.recordsTableLabel)}
              className="platform-members-table-scroll"
            >
              <Table.Content aria-label={copy.recordsTableLabel}>
                <Table.Header>
                  <Table.Column id="workspace" isRowHeader>
                    {copy.columnWorkspace}
                  </Table.Column>
                  <Table.Column id="title">{copy.columnTitle}</Table.Column>
                  <Table.Column id="status">{copy.columnStatus}</Table.Column>
                  <Table.Column id="language">{copy.columnLanguage}</Table.Column>
                  <Table.Column id="duration">{copy.columnDuration}</Table.Column>
                  <Table.Column id="created">{copy.columnCreated}</Table.Column>
                </Table.Header>
                <Table.Body items={records} dependencies={[lang]}>
                  {(record) => (
                    <Table.Row id={record.meeting_id}>
                      <Table.Cell>
                        {workspaceNames.has(record.workspace_id) ? (
                          <bdi dir="auto">{workspaceNames.get(record.workspace_id)}</bdi>
                        ) : (
                          <bdi dir="ltr">{record.workspace_id}</bdi>
                        )}
                      </Table.Cell>
                      <Table.Cell>
                        <bdi dir="auto">{record.title}</bdi>
                      </Table.Cell>
                      <Table.Cell>
                        <StatusBadge status={record.status} />
                      </Table.Cell>
                      <Table.Cell>{languageName(record.language, lang)}</Table.Cell>
                      <Table.Cell>
                        {record.duration_seconds !== null
                          ? formatDuration(record.duration_seconds, lang)
                          : copy.none}
                      </Table.Cell>
                      <Table.Cell>
                        <DayText value={record.created_at} />
                      </Table.Cell>
                    </Table.Row>
                  )}
                </Table.Body>
              </Table.Content>
            </Table.ScrollContainer>
          </Table>
        </>
      )}
    </div>
  );
}
