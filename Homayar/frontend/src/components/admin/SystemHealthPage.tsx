import { Button } from "@heroui/react";

import StatusChip from "@/components/admin/system-health/StatusChip";
import {
  AuditSinkCard,
  BackupCard,
  BuildCard,
  DependenciesCard,
  QueueCard,
  SweepsCard,
} from "@/components/admin/system-health/SystemHealthCards";
import { SYSTEM_HEALTH_COPY } from "@/components/admin/system-health/system-health-copy";
import { ageDurationText, ageSecondsBetween } from "@/components/admin/system-health/system-health-model";
import { ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useSystemHealth } from "@/lib/system-health-api";

/**
 * The system health summary for platform operators (HOM-232, endpoint
 * HOM-227). The route guard mounts this page only for an operator whose
 * workspace has `workspace:manage`, so the console sends no health request
 * for anyone else; the server decides again on its side. The page shows
 * what the API said, exactly: it never derives a status from timestamps or
 * numbers (P5), and every warning or danger status links its runbook (P7).
 */
export default function SystemHealthPage() {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  const query = useSystemHealth();
  // A failed background refresh keeps the last answer on screen (P9).
  const data = query.data;
  // P2: the checked age counts against the browser clock of this render. It
  // moves when a fetch re-renders the page, not on its own between fetches.
  const checkedAge = data ? ageSecondsBetween(data.checked_at, new Date().toISOString()) : null;

  return (
    // A div, not a main: the admin layout already provides the landmark.
    <div className="platform-page space-y-5">
      <PageHeader
        eyebrow={copy.eyebrow}
        title={copy.pageTitle}
        actions={
          <Button variant="secondary" size="sm" isPending={query.isFetching} onPress={() => void query.refetch()}>
            {copy.refresh}
          </Button>
        }
      />

      {data ? (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <StatusChip status={data.status} source="overall" />
            <p className="m-0 text-sm text-muted">
              {checkedAge === null ? copy.notReported : copy.checkedAgo(ageDurationText(checkedAge, lang))}
            </p>
          </div>
          {query.isError && <ErrorState error={query.error} />}
          <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
            <DependenciesCard health={data} />
            <QueueCard health={data} />
            <SweepsCard health={data} />
            <BackupCard health={data} />
            <AuditSinkCard health={data} />
            <BuildCard health={data} />
          </div>
        </>
      ) : query.isError ? (
        <div className="grid gap-3">
          <ErrorState error={query.error} />
          <Button
            variant="secondary"
            size="sm"
            className="justify-self-start"
            isPending={query.isFetching}
            onPress={() => void query.refetch()}
          >
            {copy.tryAgain}
          </Button>
        </div>
      ) : (
        <LoadingState label={copy.loading} />
      )}
    </div>
  );
}
