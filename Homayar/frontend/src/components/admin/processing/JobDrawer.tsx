import type { ReactNode } from "react";
import { Button, Drawer } from "@heroui/react";
import { RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components/platform/Ui";
import { PROCESSING_COPY } from "@/components/platform/processing-copy";
import { apiErrorInfo } from "@/lib/api-errors";
import { formatDate, formatNumber } from "@/lib/format";
import { failureClassLabel, jobStageLabel } from "@/lib/job-labels";
import { useI18n } from "@/lib/i18n-context";
import { useJobPolicySnapshots, useMeetingJob, useReplayJob } from "@/lib/platform-api";
import type { AdminJob, StageRun } from "@/types/platform";

/** One labelled row of the drawer's `<dl>`: the label above, the value below. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 border-b border-separator py-2 last:border-b-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="m-0 text-sm break-words">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-4">
      <h3 className="m-0 mb-2 text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

/** One entry per stage run, named by PROCESSING_COPY like the meeting page. */
function StageTimeline({ stages }: { stages: StageRun[] }) {
  const { lang } = useI18n();
  const names = PROCESSING_COPY[lang].stages;
  return (
    <ol className="m-0 grid list-none gap-2 p-0">
      {stages.map((run, index) => (
        <li
          key={`${run.stage}-${index}`}
          className="grid gap-1 border-b border-separator pb-2 last:border-b-0"
        >
          <span className="flex flex-wrap items-center gap-2">
            {/* An unknown stage is shown as sent, never guessed. */}
            <span className="text-sm">{names[run.stage] ?? run.stage}</span>
            <StatusBadge status={run.state} />
          </span>
          {run.reason_code && (
            <small className="text-xs text-muted">
              <bdi dir="ltr">{run.reason_code}</bdi>
            </small>
          )}
        </li>
      ))}
    </ol>
  );
}

/**
 * Everything known about one processing job: its fields, the stage timeline
 * of the meeting's latest job, the AI policy each attempt ran under, and the
 * replay action when the server says the job is replayable.
 */
export default function JobDrawer({ job, onClose }: { job: AdminJob | null; onClose: () => void }) {
  const { lang, dir } = useI18n();
  const isFa = lang === "fa";
  const copy = PROCESSING_COPY[lang];
  const replay = useReplayJob();
  // GET /meetings/{id}/job answers with the meeting's latest job; the stage
  // timeline is only shown when that is the job of this row.
  const jobQuery = useMeetingJob(job?.meeting_id);
  const snapshotsQuery = useJobPolicySnapshots(job?.id);
  const latest = jobQuery.data ?? null;
  const detail = latest && latest.id === job?.id ? latest : null;
  // An older row's stages are not lost, they simply belong to a newer job, and
  // only the newest one can be read. The empty text must not claim otherwise.
  const isOlderJob = latest !== null && detail === null;
  const snapshots = snapshotsQuery.data?.items ?? [];
  const count = (value: number) => formatNumber(value, lang);
  const replayErrorCode = replay.isError ? apiErrorInfo(replay.error).code : null;
  // Own keys only: a code like "constructor" would otherwise resolve to an
  // inherited property and print something that is not a message at all.
  const replayErrorText =
    replayErrorCode !== null && Object.hasOwn(copy.replayErrors, replayErrorCode)
      ? copy.replayErrors[replayErrorCode]
      : copy.replayFailed;

  return (
    <Drawer.Backdrop
      isOpen={job !== null}
      onOpenChange={(open) => {
        if (open) return;
        replay.reset();
        onClose();
      }}
    >
      <Drawer.Content placement={dir === "rtl" ? "left" : "right"} className="justify-end">
        <Drawer.Dialog
          aria-label={isFa ? "جزئیات پردازش" : "Job details"}
          className="w-[28rem] max-w-[90vw] p-4"
        >
          <Drawer.CloseTrigger aria-label={isFa ? "بستن جزئیات" : "Close details"} />
          {job && (
            <>
              <Drawer.Header>
                <Drawer.Heading>
                  {/* User content: no forced direction, the browser reads the
                      title's own order. */}
                  <bdi>{job.meeting_title}</bdi>
                </Drawer.Heading>
              </Drawer.Header>
              <Drawer.Body>
                <dl className="m-0 grid">
                  <Field label={isFa ? "وضعیت" : "State"}>
                    <StatusBadge status={job.state} />
                  </Field>
                  <Field label={isFa ? "مرحله" : "Stage"}>{jobStageLabel(job.stage, lang)}</Field>
                  <Field label={isFa ? "تلاش‌ها" : "Attempts"}>
                    {`${count(job.attempt_count)}/${count(job.max_attempts)}`}
                  </Field>
                  <Field label={isFa ? "پردازش دوباره" : "Replays"}>
                    {`${count(job.replay_count)}/${count(job.max_replays)}`}
                  </Field>
                  <Field label={isFa ? "دستهٔ خطا" : "Failure class"}>
                    {job.failure_class
                      ? failureClassLabel(job.failure_class, lang)
                      : (isFa ? "بدون خطا" : "None")}
                  </Field>
                  <Field label={isFa ? "کد خطا" : "Failure code"}>
                    {job.failure_code
                      ? <bdi dir="ltr" className="font-mono text-xs">{job.failure_code}</bdi>
                      : (isFa ? "بدون خطا" : "None")}
                  </Field>
                  <Field label={isFa ? "پیام خطا" : "Failure message"}>
                    {job.failure_message
                      ? <bdi dir="auto">{job.failure_message}</bdi>
                      : (isFa ? "ثبت نشده" : "Not recorded")}
                  </Field>
                  <Field label={isFa ? "آخرین تغییر" : "Updated"}>
                    <time dateTime={job.updated_at}>{formatDate(job.updated_at, lang)}</time>
                  </Field>
                  <Field label={isFa ? "جلسه" : "Meeting"}>
                    <Link to={`/meetings/${job.meeting_id}`}>
                      {isFa ? "باز کردن جلسه" : "Open the meeting"}
                    </Link>
                  </Field>
                  <Field label={isFa ? "شناسهٔ پردازش" : "Job id"}>
                    <bdi dir="ltr" className="font-mono text-xs">{job.id}</bdi>
                  </Field>
                </dl>

                <Section title={isFa ? "مرحله‌های پردازش" : "Stage timeline"}>
                  {jobQuery.isPending ? (
                    <LoadingState label={isFa ? "در حال دریافت مرحله‌ها" : "Loading the stages"} />
                  ) : jobQuery.isError ? (
                    <ErrorState
                      error={jobQuery.error}
                      fallback={isFa ? "دریافت مرحله‌ها ناموفق بود." : "Could not load the stages."}
                    />
                  ) : detail && detail.stages.length > 0 ? (
                    <StageTimeline stages={detail.stages} />
                  ) : isOlderJob ? (
                    <EmptyState
                      label={isFa
                        ? "این پردازش، آخرین پردازش این جلسه نیست؛ مرحله‌های آن در دسترس نیست."
                        : "This job is not the meeting's latest one, so its stages are not available."}
                    />
                  ) : (
                    <EmptyState
                      label={isFa ? "هنوز مرحله‌ای ثبت نشده است." : "No stages have been recorded yet."}
                    />
                  )}
                </Section>

                <Section title={isFa ? "سیاست هوش مصنوعی هر تلاش" : "AI policy per attempt"}>
                  {snapshotsQuery.isPending ? (
                    <LoadingState label={isFa ? "در حال دریافت سیاست‌ها" : "Loading the policies"} />
                  ) : snapshotsQuery.isError ? (
                    <ErrorState
                      error={snapshotsQuery.error}
                      fallback={isFa ? "دریافت سیاست‌ها ناموفق بود." : "Could not load the policies."}
                    />
                  ) : snapshots.length === 0 ? (
                    <EmptyState
                      label={isFa
                        ? "سیاستی برای این پردازش ثبت نشده است."
                        : "No policy was recorded for this job."}
                    />
                  ) : (
                    <ul className="m-0 grid list-none gap-2 p-0">
                      {snapshots.map((snapshot, index) => (
                        <li
                          key={index}
                          className="grid gap-0.5 border-b border-separator pb-2 last:border-b-0"
                        >
                          <span className="text-sm">
                            {isFa
                              ? `تلاش ${count(snapshot.attempt)}`
                              : `Attempt ${count(snapshot.attempt)}`}
                          </span>
                          <small className="text-xs text-muted">
                            {isFa
                              ? `نسخهٔ سیاست ${count(snapshot.policy_version)}`
                              : `Policy version ${count(snapshot.policy_version)}`}
                          </small>
                          <bdi dir="ltr" className="font-mono text-xs text-muted">
                            {snapshot.policy_digest.slice(0, 12)}
                          </bdi>
                        </li>
                      ))}
                    </ul>
                  )}
                </Section>

                {job.replayable && (
                  <div className="mt-4 grid gap-2">
                    <Button
                      className="justify-self-start"
                      isDisabled={replay.isPending || replay.isSuccess}
                      onPress={() => replay.mutate({ jobId: job.id, meetingId: job.meeting_id })}
                    >
                      <RotateCcw size={15} aria-hidden="true" />
                      {copy.replay}
                    </Button>
                    <small className="text-xs text-muted">{copy.replayHint}</small>
                  </div>
                )}
                {replay.isSuccess && (
                  <p className="mt-2 mb-0 text-sm" role="status">
                    {copy.replayQueued}
                  </p>
                )}
                {replay.isError && (
                  <p className="mt-2 mb-0 text-sm" role="alert">
                    {replayErrorText}
                  </p>
                )}
              </Drawer.Body>
            </>
          )}
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  );
}
