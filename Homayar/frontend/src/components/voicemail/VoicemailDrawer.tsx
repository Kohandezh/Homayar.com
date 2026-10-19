import type { ReactNode } from "react";
import { Alert, Button, Chip, Drawer } from "@heroui/react";
import { Link } from "react-router";

import { EmptyState, ErrorState, LoadingState } from "@/components/platform/Ui";
import {
  followUpStateLabel,
  voicemailCategoryLabel,
  voicemailPriorityLabel,
  voicemailStatusLabel,
  VOICEMAIL_COPY,
} from "@/components/voicemail/voicemail-copy";
import { apiErrorInfo } from "@/lib/api-errors";
import { formatDate, formatDuration, formatNumber, isolateLtr } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useActions } from "@/lib/platform-api";
import { statusLabel } from "@/lib/status-labels";
import { useCan } from "@/lib/use-can";
import { useUpdateVoicemail, useVoicemail } from "@/lib/voicemail-api";
import type { VoicemailDetail } from "@/types/voicemail";

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

/** The caller of a voicemail: its number in an LTR isolate, or the unknown text. */
function callerText(detail: Pick<VoicemailDetail, "caller_number">, lang: "fa" | "en") {
  const copy = VOICEMAIL_COPY[lang];
  return detail.caller_number ? isolateLtr(detail.caller_number) : copy.unknownCaller;
}

/** The classifier's confidence as a percent; the API sends a 0..1 fraction. */
function confidencePercent(value: number, lang: "fa" | "en") {
  return `${formatNumber(Math.round(value * 100), lang)}%`;
}

/**
 * Everything known about one voicemail: its fields, the transcript excerpt,
 * the follow-up action it produced, and the mark-handled / reopen write.
 * A 404 means the voicemail is gone or outside the caller's record scope.
 */
export default function VoicemailDrawer({
  voicemailId,
  onClose,
}: {
  voicemailId: string | null;
  onClose: () => void;
}) {
  const { lang, dir } = useI18n();
  const copy = VOICEMAIL_COPY[lang];
  const { has } = useCan();
  const canUpdate = has("meeting:update");
  const detailQuery = useVoicemail(voicemailId);
  const detail = detailQuery.data ?? null;
  // The follow-up action is read only when this voicemail has one; the link
  // between them is the id the API recorded.
  const actionId = detail?.follow_up_action_id ?? null;
  const actionsQuery = useActions(detail?.follow_up_action_id ? detail.meeting_id : null);
  const followUpAction = actionId
    ? (actionsQuery.data ?? []).find((action) => action.id === actionId) ?? null
    : null;
  const update = useUpdateVoicemail();
  const detailError = detailQuery.isError ? apiErrorInfo(detailQuery.error) : null;
  const updateError = update.isError ? apiErrorInfo(update.error) : null;
  // The alert belongs to the write that failed, not to whatever voicemail the
  // URL holds next: Back can change the open id without closing the drawer.
  const failedWriteForOpenVoicemail = update.isError && update.variables?.id === voicemailId;
  const updateErrorText =
    updateError?.status === 403
      ? copy.forbidden
      : updateError?.status === 404
        ? copy.notFound
        : copy.updateFailed;

  return (
    <Drawer.Backdrop
      isOpen={voicemailId !== null}
      onOpenChange={(open) => {
        if (open) return;
        update.reset();
        onClose();
      }}
    >
      <Drawer.Content placement={dir === "rtl" ? "left" : "right"} className="justify-end">
        <Drawer.Dialog
          aria-label={copy.drawerLabel}
          className="w-[28rem] max-w-[90vw] p-4"
        >
          <Drawer.CloseTrigger aria-label={copy.closeDetails} />
          {voicemailId && detailQuery.isPending && (
            <LoadingState label={copy.loading} />
          )}
          {voicemailId && detailQuery.isError && detailError?.status === 404 && (
            /* Unknown id, or a voicemail outside the caller's record scope. */
            <EmptyState label={copy.notFound} />
          )}
          {voicemailId && detailQuery.isError && detailError?.status !== 404 && (
            <ErrorState error={detailQuery.error} fallback={copy.loadFailed} />
          )}
          {detail && (
            <>
              <Drawer.Header>
                <Drawer.Heading>
                  {/* User content: no forced direction, the browser reads the
                      number's own order inside the isolate marks. */}
                  <bdi>{callerText(detail, lang)}</bdi>
                  <span className="block text-sm font-normal text-muted">
                    <time dateTime={detail.received_at}>
                      {formatDate(detail.received_at, lang)}
                    </time>
                  </span>
                </Drawer.Heading>
              </Drawer.Header>
              <Drawer.Body>
                <dl className="m-0 grid">
                  <Field label={copy.fieldMailbox}>
                    {detail.mailbox ? (
                      <bdi dir="ltr" className="font-mono text-xs">{detail.mailbox}</bdi>
                    ) : (
                      copy.notRecorded
                    )}
                  </Field>
                  <Field label={copy.fieldExtension}>
                    {detail.extension ? (
                      <bdi dir="ltr" className="font-mono text-xs">{detail.extension}</bdi>
                    ) : (
                      copy.notRecorded
                    )}
                  </Field>
                  <Field label={copy.fieldDuration}>
                    {formatDuration(detail.duration_seconds, lang)}
                  </Field>
                  <Field label={copy.fieldStatus}>
                    {voicemailStatusLabel(detail.status, lang)}
                  </Field>
                  <Field label={copy.fieldCategory}>
                    {detail.category ? voicemailCategoryLabel(detail.category, lang) : copy.notClassified}
                  </Field>
                  <Field label={copy.fieldIntent}>
                    {detail.intent ? <bdi dir="auto">{detail.intent}</bdi> : copy.notRecorded}
                  </Field>
                  <Field label={copy.fieldTopics}>
                    {detail.topics.length > 0 ? (
                      <span className="flex flex-wrap gap-1.5">
                        {detail.topics.map((topic) => (
                          <Chip key={topic} size="sm" variant="soft">
                            <Chip.Label>
                              <bdi dir="auto">{topic}</bdi>
                            </Chip.Label>
                          </Chip>
                        ))}
                      </span>
                    ) : (
                      copy.noTopics
                    )}
                  </Field>
                  <Field label={copy.fieldPriority}>
                    {detail.priority ? voicemailPriorityLabel(detail.priority, lang) : copy.notClassified}
                  </Field>
                  {detail.priority_score !== null && (
                    <Field label={copy.fieldPriorityScore}>
                      {formatNumber(detail.priority_score, lang)}
                    </Field>
                  )}
                  <Field label={copy.fieldConfidence}>
                    {detail.classification_confidence !== null
                      ? confidencePercent(detail.classification_confidence, lang)
                      : copy.notRecorded}
                  </Field>
                  <Field label={copy.fieldClassifier}>
                    {detail.classifier_kind ? (
                      <bdi dir="ltr" className="font-mono text-xs">{detail.classifier_kind}</bdi>
                    ) : (
                      copy.notRecorded
                    )}
                  </Field>
                  <Field label={copy.fieldMeetingStatus}>
                    <bdi dir="auto">{statusLabel(detail.meeting_status, lang)}</bdi>
                  </Field>
                </dl>

                <Section title={copy.transcriptTitle}>
                  {detail.transcript_excerpt ? (
                    /* Caller speech: the browser reads its own direction. */
                    <p className="m-0 text-sm" dir="auto">
                      {detail.transcript_excerpt}
                    </p>
                  ) : (
                    <EmptyState label={copy.noTranscript} />
                  )}
                </Section>

                <Section title={copy.followUpTitle}>
                  <dl className="m-0 grid">
                    <Field label={copy.fieldStatus}>{followUpStateLabel(detail.follow_up_state, lang)}</Field>
                    {detail.follow_up_done_at && (
                      <Field label={copy.followUpHandledAt}>
                        <time dateTime={detail.follow_up_done_at}>
                          {formatDate(detail.follow_up_done_at, lang)}
                        </time>
                      </Field>
                    )}
                    {actionId && (
                      <Field label={copy.followUpAction}>
                        {actionsQuery.isPending ? (
                          <LoadingState label={copy.loading} />
                        ) : followUpAction ? (
                          <span className="grid gap-0.5">
                            <bdi dir="auto">{followUpAction.description}</bdi>
                            <small className="text-xs text-muted">
                              {statusLabel(followUpAction.status, lang)}
                            </small>
                          </span>
                        ) : (
                          copy.actionNotAvailable
                        )}
                      </Field>
                    )}
                  </dl>
                  <p className="mb-0 mt-2">
                    <Link to={`/meetings/${detail.meeting_id}`}>{copy.openCall}</Link>
                  </p>

                  {/* A viewer (no meeting:update) gets no write control at all. */}
                  {canUpdate && detail.follow_up_state !== "done" && (
                    <Button
                      className="mt-2"
                      isDisabled={update.isPending}
                      onPress={() => update.mutate({ id: detail.id, patch: { follow_up_state: "done" } })}
                    >
                      {copy.markHandled}
                    </Button>
                  )}
                  {canUpdate && detail.follow_up_state === "done" && (
                    <Button
                      className="mt-2"
                      isDisabled={update.isPending}
                      onPress={() => update.mutate({ id: detail.id, patch: { follow_up_state: "pending" } })}
                    >
                      {copy.reopen}
                    </Button>
                  )}
                  {failedWriteForOpenVoicemail && (
                    <Alert status="danger" role="alert" className="mt-2">
                      <Alert.Indicator aria-hidden="true" />
                      <Alert.Content>
                        <Alert.Description>{updateErrorText}</Alert.Description>
                      </Alert.Content>
                    </Alert>
                  )}
                </Section>
              </Drawer.Body>
            </>
          )}
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  );
}
