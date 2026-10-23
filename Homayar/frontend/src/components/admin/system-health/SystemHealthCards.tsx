import type { ReactNode } from "react";

import StatusChip from "@/components/admin/system-health/StatusChip";
import SystemHealthCard from "@/components/admin/system-health/SystemHealthCard";
import { SYSTEM_HEALTH_COPY } from "@/components/admin/system-health/system-health-copy";
import { ageDurationText, ageSecondsBetween } from "@/components/admin/system-health/system-health-model";
import { formatAudioSeconds, formatDate, formatNumber, isolateLtr } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { SystemHealthDependency, SystemHealthResponse } from "@/types/system-health";

/** One term and its value inside a card's list. */
function Pair({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-muted">{term}</dt>
      <dd className="m-0 text-sm">{children}</dd>
    </div>
  );
}

/**
 * A timestamp of the answer as an age before `checkedAt` (P6): the absolute
 * date sits on the <time> element's dateTime and title. A missing timestamp
 * is Not reported, never "0 seconds ago" (P5).
 */
function AgeAt({ timestamp, checkedAt }: { timestamp: string | null; checkedAt: string }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  const seconds = ageSecondsBetween(timestamp, checkedAt);
  if (seconds === null || timestamp === null) return <>{copy.notReported}</>;
  return (
    <time dateTime={timestamp} title={formatDate(timestamp, lang)} className="tabular-nums">
      {copy.ago(ageDurationText(seconds, lang))}
    </time>
  );
}

/** An identifier the API sent (a SHA, a version, a failure class), or Not reported. */
function Identifier({ value }: { value: string | null }) {
  const { lang } = useI18n();
  if (value === null) return <>{SYSTEM_HEALTH_COPY[lang].notReported}</>;
  return <span className="tabular-nums">{isolateLtr(value)}</span>;
}

/** A whole number, or Not reported when the source failed (P5). */
function Count({ value }: { value: number | null }) {
  const { lang } = useI18n();
  if (value === null) return <>{SYSTEM_HEALTH_COPY[lang].notReported}</>;
  return <span className="tabular-nums">{formatNumber(value, lang)}</span>;
}

/** A duration in seconds (queue ages, sweep intervals), or Not reported. */
function Duration({ seconds }: { seconds: number | null }) {
  const { lang } = useI18n();
  if (seconds === null) return <>{SYSTEM_HEALTH_COPY[lang].notReported}</>;
  return <span className="tabular-nums">{formatAudioSeconds(seconds, lang)}</span>;
}

function DependencyRow({ dep, source, term }: { dep: SystemHealthDependency; source: "database" | "redis" | "storage"; term: string }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  return (
    <Pair term={term}>
      <span className="flex flex-wrap items-center gap-2">
        <StatusChip status={dep.status} source={source} />
        <span className="text-sm text-muted tabular-nums">
          {dep.probe_seconds === null ? copy.notReported : copy.probeSeconds(formatNumber(dep.probe_seconds, lang, 3))}
        </span>
      </span>
    </Pair>
  );
}

export function DependenciesCard({ health }: { health: SystemHealthResponse }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  return (
    <SystemHealthCard title={copy.dependenciesTitle}>
      <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <DependencyRow dep={health.dependencies.database} source="database" term={copy.database} />
        <DependencyRow dep={health.dependencies.redis} source="redis" term={copy.redis} />
        <DependencyRow dep={health.dependencies.storage} source="storage" term={copy.storage} />
        <Pair term={copy.schema}>
          <StatusChip status={health.schema.status} source="schema" />
        </Pair>
        <Pair term={copy.codeHead}>
          <Identifier value={health.schema.code_head} />
        </Pair>
        <Pair term={copy.databaseRevision}>
          <Identifier value={health.schema.database_revision} />
        </Pair>
      </dl>
    </SystemHealthCard>
  );
}

export function QueueCard({ health }: { health: SystemHealthResponse }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  return (
    <SystemHealthCard title={copy.queueTitle}>
      <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <Pair term={copy.status}>
          <StatusChip status={health.queue.status} source="queue" />
        </Pair>
        <Pair term={copy.queuedDue}>
          <Count value={health.queue.queued_due} />
        </Pair>
        <Pair term={copy.oldestQueuedAge}>
          <Duration seconds={health.queue.oldest_queued_due_age_seconds} />
        </Pair>
        <Pair term={copy.retryScheduled}>
          <Count value={health.queue.retry_scheduled} />
        </Pair>
        <Pair term={copy.brokerQueueLength}>
          <Count value={health.queue.broker_queue_length} />
        </Pair>
      </dl>
    </SystemHealthCard>
  );
}

export function SweepsCard({ health }: { health: SystemHealthResponse }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  return (
    <SystemHealthCard title={copy.sweepsTitle}>
      <div className="flex flex-col gap-4">
        <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
          <Pair term={copy.status}>
            <StatusChip status={health.sweeps.status} source="sweeps" />
          </Pair>
        </dl>
        <ul className="m-0 flex list-none flex-col gap-4 p-0">
          {health.sweeps.tasks.map((task) => (
            <li key={task.task} className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">{isolateLtr(task.task)}</span>
                <StatusChip status={task.status} source="sweeps" />
              </div>
              <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-3">
                <Pair term={copy.lastSuccess}>
                  <AgeAt timestamp={task.last_success_at} checkedAt={health.checked_at} />
                </Pair>
                <Pair term={copy.expectedInterval}>
                  <Duration seconds={task.expected_interval_seconds} />
                </Pair>
                {task.last_failure_class !== null && (
                  <Pair term={copy.failureClass}>
                    <Identifier value={task.last_failure_class} />
                  </Pair>
                )}
              </dl>
            </li>
          ))}
        </ul>
      </div>
    </SystemHealthCard>
  );
}

export function BackupCard({ health }: { health: SystemHealthResponse }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  return (
    <SystemHealthCard title={copy.backupTitle}>
      <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <Pair term={copy.status}>
          <StatusChip status={health.backup.status} source="backup" />
        </Pair>
        <Pair term={copy.backupPostgres}>
          <AgeAt timestamp={health.backup.targets.postgres} checkedAt={health.checked_at} />
        </Pair>
        <Pair term={copy.backupObjects}>
          <AgeAt timestamp={health.backup.targets.objects} checkedAt={health.checked_at} />
        </Pair>
        <Pair term={copy.backupOffsite}>
          <AgeAt timestamp={health.backup.targets.offsite} checkedAt={health.checked_at} />
        </Pair>
      </dl>
    </SystemHealthCard>
  );
}

export function AuditSinkCard({ health }: { health: SystemHealthResponse }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  return (
    <SystemHealthCard title={copy.auditTitle}>
      <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <Pair term={copy.status}>
          <StatusChip status={health.audit_sink.status} source="audit" />
        </Pair>
        <Pair term={copy.lastVerify}>
          <AgeAt timestamp={health.audit_sink.last_verify_at} checkedAt={health.checked_at} />
        </Pair>
        <Pair term={copy.lastSuccessVerify}>
          <AgeAt timestamp={health.audit_sink.last_verify_success_at} checkedAt={health.checked_at} />
        </Pair>
        {health.audit_sink.last_failure_class !== null && (
          <Pair term={copy.failureClass}>
            <Identifier value={health.audit_sink.last_failure_class} />
          </Pair>
        )}
      </dl>
    </SystemHealthCard>
  );
}

export function BuildCard({ health }: { health: SystemHealthResponse }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  return (
    <SystemHealthCard title={copy.buildTitle}>
      <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <Pair term={copy.buildVersion}>
          <Identifier value={health.build.version} />
        </Pair>
        <Pair term={copy.buildSourceSha}>
          <Identifier value={health.build.source_sha} />
        </Pair>
        <Pair term={copy.buildMigrationHead}>
          <Identifier value={health.build.migration_head} />
        </Pair>
        <Pair term={copy.buildProfile}>
          {health.build.profile === null ? copy.notReported : <span dir="auto">{health.build.profile}</span>}
        </Pair>
        <Pair term={copy.buildPythonVersion}>
          <Identifier value={health.build.python_version} />
        </Pair>
      </dl>
    </SystemHealthCard>
  );
}
