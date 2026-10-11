import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Clipboard, Eye, EyeOff, FileDown, Link2, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { apiErrorInfo } from "@/lib/api-errors";
import { useI18n } from "@/lib/i18n-context";
import { formatDate, formatDuration, formatNumber } from "@/lib/format";
import {
  platformQueryKeys,
  saveDownload,
  useActions,
  useCreateAction,
  useCreateShare,
  useDeleteAction,
  useEvidence,
  useMeetingExport,
  useMeetingSummary,
  useOriginalTranscript,
  useRenameSpeaker,
  useRevealValue,
  useRevokeShare,
  useShares,
  useTranscript,
  useUpdateAction,
} from "@/lib/platform-api";
import { useCan } from "@/lib/use-can";
import { statusLabel } from "@/lib/status-labels";
import type {
  ActionPriority,
  ActionStatus,
  EvidenceSpan,
  MeetingDetail,
  MeetingExportFormat,
  SummaryInsight,
  Transcript,
  TranscriptSegment,
} from "@/types/platform";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components/platform/Ui";
import { PROCESSING_COPY } from "@/components/platform/processing-copy";
import MaskedText from "@/components/platform/MaskedText";
import { RESULTS_COPY, type ResultsCopy, type ResultsTab } from "@/components/platform/results-copy";

type Lang = "fa" | "en";

function EvidenceBadge({ status, copy, lang }: { status: string; copy: ResultsCopy; lang: Lang }) {
  return <StatusBadge status={`evidence-${status}`} label={`${copy.summary.evidence}: ${statusLabel(status, lang)}`} />;
}

function SpeakerEditor({ name, label, saveLabel, onSave }: { name: string; label: string; saveLabel: string; onSave: (value: string) => void }) {
  const [value, setValue] = useState(name);
  return (
    <form onSubmit={(event) => { event.preventDefault(); if (value.trim()) onSave(value.trim()); }}>
      <input value={value} onChange={(event) => setValue(event.target.value)} maxLength={160} aria-label={label} />
      <button className="platform-icon-button" aria-label={saveLabel}><Check size={14} aria-hidden="true" /></button>
    </form>
  );
}

function SummaryList({
  title,
  items,
  evidence,
  copy,
  lang,
  onShowSegment,
}: {
  title: string;
  items: SummaryInsight[];
  evidence: Map<string, EvidenceSpan>;
  copy: ResultsCopy;
  lang: Lang;
  onShowSegment: (segmentId: string) => void;
}) {
  if (!items.length) return null;
  return (
    <section className="meeting-key-points">
      <h2>{title}</h2>
      {items.map((point, index) => {
        const spans = (point.evidence_ids ?? []).map((id) => evidence.get(id)).filter((span): span is EvidenceSpan => Boolean(span));
        const current = spans.find((span) => span.is_current_revision);
        return (
          <article key={`${point.category ?? "item"}-${index}`} className="platform-surface">
            {point.category && <span>{point.category}</span>}
            <p dir="auto"><MaskedText text={point.description ?? ""} copy={copy} revealed={{}} /></p>
            <div className="summary-item-meta">
              {point.evidence_status && <EvidenceBadge status={point.evidence_status} copy={copy} lang={lang} />}
              {current ? (
                <button type="button" className="platform-link-button" onClick={() => onShowSegment(current.segment_id)}>
                  <Link2 size={13} aria-hidden="true" />
                  {copy.summary.showSource}
                </button>
              ) : spans.length ? (
                <small>{copy.summary.earlierRevision}</small>
              ) : null}
            </div>
          </article>
        );
      })}
    </section>
  );
}

/** The segment playing at `atMs`: the one containing it, else the last one started before it. */
function segmentAt(segments: TranscriptSegment[], atMs: number): TranscriptSegment | null {
  let before: TranscriptSegment | null = null;
  for (const segment of segments) {
    if (segment.start_ms <= atMs && atMs < segment.end_ms) return segment;
    if (segment.start_ms <= atMs && (!before || segment.start_ms > before.start_ms)) before = segment;
  }
  return before;
}

export default function MeetingResults({ meeting, atMs = null }: { meeting: MeetingDetail; atMs?: number | null }) {
  const { lang } = useI18n();
  const copy = RESULTS_COPY[lang];
  const can = useCan();
  const queryClient = useQueryClient();
  const meetingId = meeting.id;
  const [tab, setTab] = useState<ResultsTab>("summary");
  const [exportFormat, setExportFormat] = useState<MeetingExportFormat>("txt");
  const [speakerFilter, setSpeakerFilter] = useState("");
  const [focusSegment, setFocusSegment] = useState<string | null>(null);
  const [originalMode, setOriginalMode] = useState<"off" | "confirm" | "on">("off");
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [revealTarget, setRevealTarget] = useState<{ segmentId: string; placeholder: string } | null>(null);
  const [revealReason, setRevealReason] = useState("");
  const [actionDescription, setActionDescription] = useState("");
  const [actionOwner, setActionOwner] = useState("");
  const [actionPriority, setActionPriority] = useState<ActionPriority>("medium");
  const [shareAuthentication, setShareAuthentication] = useState(false);
  const [shareHours, setShareHours] = useState(168);
  const [createdShareUrl, setCreatedShareUrl] = useState("");
  const tabRefs = useRef<Partial<Record<ResultsTab, HTMLButtonElement | null>>>({});
  const [appliedAt, setAppliedAt] = useState<number | null>(null);
  const [atOutcome, setAtOutcome] = useState<"found" | "missing" | null>(null);

  const transcript = useTranscript(meetingId);
  const original = useOriginalTranscript(originalMode === "on" ? meetingId : null);
  const summary = useMeetingSummary(meetingId);
  const evidence = useEvidence(meetingId);
  const actions = useActions(meetingId);
  const shares = useShares(can.manageShares ? meetingId : null);
  const meetingExport = useMeetingExport();
  const renameSpeaker = useRenameSpeaker();
  const createAction = useCreateAction();
  const updateAction = useUpdateAction();
  const deleteAction = useDeleteAction();
  const createShare = useCreateShare();
  const revokeShare = useRevokeShare();
  const reveal = useRevealValue();

  const tabs: ResultsTab[] = can.manageShares ? ["summary", "transcript", "actions", "shares"] : ["summary", "transcript", "actions"];
  const redacted = transcript.data?.privacy_enforced === true && transcript.data.view === "redacted";
  const showingOriginal = originalMode === "on" && Boolean(original.data);
  const shown: Transcript | undefined = showingOriginal ? original.data : transcript.data;
  const redaction = transcript.data?.redaction ?? null;
  const canReveal = redacted && !showingOriginal && can.readOriginalTranscript && Boolean(redaction?.id && redaction.mapping_retained);
  const evidenceById = new Map((evidence.data?.items ?? []).map((span) => [span.id, span]));

  // A search result's `?at=` (QA Q-01): open the transcript at the segment
  // playing at that time, once per distinct time (back/forward re-applies).
  if (atMs !== null && atMs !== appliedAt && transcript.data) {
    const target = segmentAt(transcript.data.segments, atMs);
    setAppliedAt(atMs);
    setSpeakerFilter("");
    setTab("transcript");
    setFocusSegment(target?.id ?? null);
    setAtOutcome(target ? "found" : "missing");
  }

  // Originals and revealed values never outlive the view that asked for them.
  useEffect(() => {
    return () => {
      queryClient.removeQueries({ queryKey: platformQueryKeys.transcriptOriginal(meetingId) });
    };
  }, [meetingId, queryClient]);

  useEffect(() => {
    if (!focusSegment || tab !== "transcript") return;
    const element = document.getElementById(`segment-${focusSegment}`);
    element?.scrollIntoView?.({ block: "center" });
    element?.focus();
  }, [focusSegment, tab, shown]);

  function hideOriginal() {
    setOriginalMode("off");
    queryClient.removeQueries({ queryKey: platformQueryKeys.transcriptOriginal(meetingId) });
  }

  function showSegment(segmentId: string) {
    setSpeakerFilter("");
    setTab("transcript");
    setFocusSegment(segmentId);
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    const index = tabs.indexOf(tab);
    const forward = document.documentElement.dir === "rtl" ? "ArrowLeft" : "ArrowRight";
    const backward = forward === "ArrowLeft" ? "ArrowRight" : "ArrowLeft";
    let next: number | null = null;
    if (event.key === forward) next = (index + 1) % tabs.length;
    else if (event.key === backward) next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    setTab(tabs[next]);
    tabRefs.current[tabs[next]]?.focus();
  }

  function submitReveal(event: FormEvent) {
    event.preventDefault();
    if (!revealTarget || !redaction?.id) return;
    const target = revealTarget;
    reveal.mutate(
      { meetingId, redactionId: redaction.id, placeholder: target.placeholder, reason: revealReason.trim() },
      {
        onSuccess: (result) => {
          setRevealed((current) => ({ ...current, [result.placeholder]: result.value }));
          setRevealTarget(null);
          setRevealReason("");
        },
      },
    );
  }

  function submitAction(event: FormEvent) {
    event.preventDefault();
    const description = actionDescription.trim();
    if (!description) return;
    createAction.mutate(
      { meetingId, input: { description, assignee_text: actionOwner.trim() || null, priority: actionPriority } },
      { onSuccess: () => { setActionDescription(""); setActionOwner(""); setActionPriority("medium"); } },
    );
  }

  function issueShare() {
    createShare.mutate(
      { meetingId, input: { expires_in_hours: shareHours, require_authentication: shareAuthentication } },
      { onSuccess: (share) => setCreatedShareUrl(share.url) },
    );
  }

  const summaryMissing = summary.isError && apiErrorInfo(summary.error).code === "summary_not_ready";
  // Each tab states the view of its own response (gap G10), never the
  // transcript's.
  const summaryView = summary.data?.view ?? null;
  const summaryEnforced = summary.data?.privacy_enforced === true;
  const actionsRedacted = (actions.data ?? []).some((action) => action.view === "redacted");
  const actionsOriginalEnforced = (actions.data ?? []).some(
    (action) => action.view === "original" && action.privacy_enforced === true,
  );
  const analysisReason = meeting.quality?.degradations.find((item) => item.stage === "intelligence")?.reason
    ?? meeting.latest_job?.stages.find((run) => run.stage === "intelligence" && run.reason_code)?.reason_code
    ?? null;
  const originalError = original.isError ? apiErrorInfo(original.error).code : null;
  const revealCode = reveal.isError ? apiErrorInfo(reveal.error).code : null;
  const segments = (shown?.segments ?? []).filter((segment) => !speakerFilter || segment.speaker_key === speakerFilter);
  const operationError =
    meetingExport.error ?? createAction.error ?? updateAction.error ?? deleteAction.error ?? renameSpeaker.error ?? createShare.error ?? revokeShare.error;

  return (
    <div className="meeting-results">
      {redacted && (
        <section className="privacy-notice" aria-label={copy.transcript.privacyTitle}>
          <ShieldCheck size={16} aria-hidden="true" />
          <div>
            <p>
              {copy.transcript.masked(
                formatNumber(redaction?.entity_count ?? 0, lang),
                (redaction?.classes ?? []).map((value) => copy.transcript.classes[value] ?? value).join(lang === "fa" ? "، " : ", "),
              )}
            </p>
            <p>{copy.transcript.maskedLimits}</p>
            {can.readOriginalTranscript && originalMode === "off" && (
              <button type="button" className="platform-button" onClick={() => setOriginalMode("confirm")}>
                <Eye size={15} aria-hidden="true" />
                {copy.transcript.showOriginal}
              </button>
            )}
            {originalMode === "confirm" && (
              <div className="privacy-confirm" role="alertdialog" aria-label={copy.transcript.showOriginal}>
                <p>{copy.transcript.confirmOriginal}</p>
                <button
                  type="button"
                  className="platform-button platform-button-primary"
                  onClick={() => { setOriginalMode("on"); setTab("transcript"); }}
                  autoFocus
                >
                  {copy.transcript.confirmOriginalAction}
                </button>
                <button type="button" className="platform-button" onClick={() => setOriginalMode("off")}>{copy.transcript.cancel}</button>
              </div>
            )}
            {showingOriginal && (
              <div className="privacy-original" role="status">
                <p>{copy.transcript.viewingOriginal}</p>
                <button type="button" className="platform-button" onClick={hideOriginal}>
                  <EyeOff size={15} aria-hidden="true" />
                  {copy.transcript.hideOriginal}
                </button>
              </div>
            )}
            {originalError && (
              <p role="alert" className="privacy-error">
                {originalError === "original_access_denied" ? copy.transcript.originalDenied : copy.transcript.originalFailed}
              </p>
            )}
          </div>
        </section>
      )}

      <div className="meeting-detail-toolbar">
        <div className="meeting-tabs" role="tablist" aria-label={copy.tabList}>
          {tabs.map((item) => (
            <button
              key={item}
              ref={(element) => { tabRefs.current[item] = element; }}
              id={`meeting-tab-${item}`}
              role="tab"
              type="button"
              aria-selected={tab === item}
              aria-controls={`meeting-panel-${item}`}
              tabIndex={tab === item ? 0 : -1}
              className={tab === item ? "is-active" : ""}
              onClick={() => setTab(item)}
              onKeyDown={onTabKey}
            >
              {copy.tabs[item]}
            </button>
          ))}
        </div>
        <div className="meeting-export-control">
          <select value={exportFormat} onChange={(event) => setExportFormat(event.target.value as MeetingExportFormat)} aria-label={copy.exportFormat}>
            <option value="txt">TXT</option>
            <option value="json">JSON</option>
            <option value="srt">SRT</option>
            <option value="vtt">VTT</option>
          </select>
          <button
            type="button"
            className="platform-icon-button"
            onClick={() => meetingExport.mutate({ meetingId, format: exportFormat }, { onSuccess: saveDownload })}
            disabled={meetingExport.isPending}
            title={copy.exportAction}
            aria-label={copy.exportAction}
          >
            <FileDown size={15} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div id={`meeting-panel-${tab}`} role="tabpanel" aria-labelledby={`meeting-tab-${tab}`} tabIndex={0} className="meeting-tab-panel">
        {tab === "summary" && (
          <div className="meeting-summary-view">
            {summary.isLoading ? (
              <LoadingState label={copy.loading} />
            ) : summaryMissing ? (
              <div className="platform-state platform-empty-state" role="status">
                <span>
                  {copy.summary.notReady}{" "}
                  {analysisReason && copy.summary.unavailableBecause(PROCESSING_COPY[lang].reasons[analysisReason] ?? analysisReason)}
                </span>
              </div>
            ) : summary.isError ? (
              <ErrorState error={summary.error} fallback={copy.summary.failed} />
            ) : summary.data ? (
              <>
                <section className="platform-surface meeting-summary-overview">
                  <h2>
                    {copy.summary.overview}
                    {summary.data.evidence_status && <EvidenceBadge status={summary.data.evidence_status} copy={copy} lang={lang} />}
                  </h2>
                  {summaryView === "redacted" && <p className="content-view-note">{copy.contentView.summaryRedacted}</p>}
                  {summaryView === "original" && summaryEnforced && <p className="content-view-note">{copy.contentView.summaryOriginal}</p>}
                  {summary.data.analysis_run?.input_view === "redacted" && (
                    <p className="content-view-note">{copy.contentView.analysisOnRedacted}</p>
                  )}
                  <p dir="auto"><MaskedText text={summary.data.overview} copy={copy} revealed={{}} /></p>
                  {summary.data.analysis_run?.is_mock && <p className="meeting-stage-mock">{copy.summary.mock}</p>}
                  {summary.data.topics.length > 0 && (
                    <div className="meeting-topics" aria-label={copy.summary.topics}>
                      {summary.data.topics.map((topic) => <span key={topic} dir="auto">{topic}</span>)}
                    </div>
                  )}
                </section>
                <SummaryList title={copy.summary.decisions} items={summary.data.decisions} evidence={evidenceById} copy={copy} lang={lang} onShowSegment={showSegment} />
                <SummaryList title={copy.summary.keyPoints} items={summary.data.key_points} evidence={evidenceById} copy={copy} lang={lang} onShowSegment={showSegment} />
              </>
            ) : null}
          </div>
        )}

        {tab === "transcript" && (
          <div className="meeting-transcript-view">
            {atOutcome && appliedAt !== null && (
              <p className="content-view-note" role="status">
                {atOutcome === "found" ? copy.transcript.atFound(formatDuration(appliedAt / 1000, lang)) : copy.transcript.atMissing}
              </p>
            )}
            {transcript.isLoading || (originalMode === "on" && original.isLoading) ? (
              <LoadingState label={copy.loading} />
            ) : transcript.isError ? (
              <ErrorState error={transcript.error} fallback={copy.transcript.failed} />
            ) : !shown?.segments.length ? (
              <EmptyState label={copy.transcript.empty} />
            ) : (
              <>
                <div className="transcript-toolbar">
                  <label>
                    <span>{copy.transcript.speakerFilter}</span>
                    <select value={speakerFilter} onChange={(event) => setSpeakerFilter(event.target.value)}>
                      <option value="">{copy.transcript.allSpeakers}</option>
                      {shown.speakers.map((speaker) => (
                        <option key={speaker.id} value={speaker.speaker_key}>{speaker.display_name ?? speaker.speaker_key}</option>
                      ))}
                    </select>
                  </label>
                  {Object.keys(revealed).length > 0 && (
                    <button type="button" className="platform-button" onClick={() => setRevealed({})}>
                      <EyeOff size={15} aria-hidden="true" />
                      {copy.transcript.hideValue}
                    </button>
                  )}
                </div>
                {can.updateMeeting && shown.speakers.length > 0 && (
                  <div className="meeting-speaker-list" aria-label={copy.transcript.speakers}>
                    {shown.speakers.map((speaker) => {
                      const name = speaker.display_name ?? speaker.speaker_key;
                      return (
                        <SpeakerEditor
                          key={speaker.id}
                          name={name}
                          label={copy.transcript.renameSpeaker(name)}
                          saveLabel={copy.transcript.saveSpeaker}
                          onSave={(displayName) => renameSpeaker.mutate({ meetingId, speakerId: speaker.id, displayName })}
                        />
                      );
                    })}
                  </div>
                )}
                <div className="meeting-segments">
                  {segments.map((segment) => (
                    <article id={`segment-${segment.id}`} key={segment.id} className="meeting-segment" tabIndex={-1}>
                      <div>
                        <strong dir="auto">{segment.speaker_name}</strong>
                        <time dir="ltr">{formatDuration(segment.start_ms / 1000, lang)}</time>
                      </div>
                      <p dir="auto">
                        <MaskedText
                          text={segment.text}
                          copy={copy}
                          revealed={revealed}
                          onPick={canReveal ? (placeholder) => { reveal.reset(); setRevealTarget({ segmentId: segment.id, placeholder }); } : undefined}
                        />
                      </p>
                      {revealTarget?.segmentId === segment.id && (
                        <form className="pii-reveal-form" onSubmit={submitReveal}>
                          <label>
                            <span>{copy.transcript.revealReason}</span>
                            <input
                              className="platform-input"
                              value={revealReason}
                              onChange={(event) => setRevealReason(event.target.value)}
                              minLength={3}
                              maxLength={200}
                              required
                              autoFocus
                            />
                          </label>
                          <button className="platform-button platform-button-primary" disabled={reveal.isPending}>
                            {copy.transcript.revealSubmit} <bdi dir="ltr">{revealTarget.placeholder}</bdi>
                          </button>
                          <button type="button" className="platform-button" onClick={() => setRevealTarget(null)}>{copy.transcript.cancel}</button>
                          {reveal.isError && (
                            <p role="alert">{(revealCode && copy.transcript.revealErrors[revealCode]) || copy.transcript.revealFailed}</p>
                          )}
                        </form>
                      )}
                    </article>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {tab === "actions" && (
          <div className="meeting-actions-view">
            {actionsRedacted && <p className="content-view-note">{copy.contentView.actionsRedacted}</p>}
            {actionsOriginalEnforced && <p className="content-view-note">{copy.contentView.actionsOriginal}</p>}
            {can.updateMeeting && (
              <form className="platform-surface action-create-form" onSubmit={submitAction}>
                <input className="platform-input" value={actionDescription} onChange={(event) => setActionDescription(event.target.value)} placeholder={copy.actions.description} aria-label={copy.actions.description} maxLength={2000} required />
                <input className="platform-input" value={actionOwner} onChange={(event) => setActionOwner(event.target.value)} placeholder={copy.actions.owner} aria-label={copy.actions.owner} maxLength={200} />
                <select className="platform-select" value={actionPriority} onChange={(event) => setActionPriority(event.target.value as ActionPriority)} aria-label={copy.actions.priority}>
                  {(["low", "medium", "high"] as ActionPriority[]).map((value) => <option key={value} value={value}>{statusLabel(value, lang)}</option>)}
                </select>
                <button className="platform-button platform-button-primary" disabled={createAction.isPending}><Plus size={15} aria-hidden="true" />{copy.actions.add}</button>
              </form>
            )}
            {actions.isLoading ? <LoadingState label={copy.loading} /> : actions.isError ? <ErrorState error={actions.error} fallback={copy.actions.failed} /> : !actions.data?.length ? <EmptyState label={copy.actions.empty} /> : (
              <div className="action-list">
                {actions.data.map((action) => {
                  const source = action.source_segment_id ?? action.evidence?.[0]?.segment_id ?? null;
                  return (
                    <article key={action.id} className="platform-surface action-row">
                      <div className="action-main">
                        <span className={`action-priority action-priority-${action.priority}`}>{statusLabel(action.priority, lang)}</span>
                        <div>
                          <strong dir="auto"><MaskedText text={action.description} copy={copy} revealed={{}} /></strong>
                          {action.assignee_text && <span dir="auto"><MaskedText text={action.assignee_text} copy={copy} revealed={{}} /></span>}
                          {action.evidence_status && <EvidenceBadge status={action.evidence_status} copy={copy} lang={lang} />}
                        </div>
                      </div>
                      <div className="action-controls">
                        {source && (
                          <button type="button" className="platform-icon-button" onClick={() => showSegment(source)} title={copy.actions.showSource} aria-label={copy.actions.showSource}>
                            <Link2 size={14} aria-hidden="true" />
                          </button>
                        )}
                        {can.updateMeeting ? (
                          <>
                            <select value={action.status} onChange={(event) => updateAction.mutate({ meetingId, actionId: action.id, input: { status: event.target.value as ActionStatus } })} aria-label={copy.actions.status}>
                              {(["open", "in_progress", "done", "cancelled"] as ActionStatus[]).map((value) => <option key={value} value={value}>{statusLabel(value, lang)}</option>)}
                            </select>
                            <button type="button" className="platform-icon-button platform-button-danger" onClick={() => deleteAction.mutate({ meetingId, actionId: action.id })} aria-label={copy.actions.delete}>
                              <Trash2 size={14} aria-hidden="true" />
                            </button>
                          </>
                        ) : <StatusBadge status={action.status} />}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {tab === "shares" && can.manageShares && (
          <div className="meeting-shares-view">
            <div className="platform-surface share-create-row">
              <label>
                <span>{copy.shares.expires}</span>
                <select value={shareHours} onChange={(event) => setShareHours(Number(event.target.value))}>
                  {[24, 168, 720].map((hours) => <option key={hours} value={hours}>{copy.shares.hours[hours]}</option>)}
                </select>
              </label>
              <label className="share-auth-toggle">
                <input type="checkbox" checked={shareAuthentication} onChange={(event) => setShareAuthentication(event.target.checked)} />
                <span>{copy.shares.requireSignIn}</span>
              </label>
              <button type="button" className="platform-button platform-button-primary" onClick={issueShare} disabled={createShare.isPending}>
                <Link2 size={15} aria-hidden="true" />
                {copy.shares.create}
              </button>
            </div>
            <p className="share-note">{copy.shares.revokeNote}</p>
            {createdShareUrl && (
              <div className="created-share-link">
                <a href={createdShareUrl} target="_blank" rel="noreferrer" dir="ltr">{createdShareUrl}</a>
                <button type="button" className="platform-icon-button" onClick={() => navigator.clipboard.writeText(createdShareUrl)} aria-label={copy.shares.copy}>
                  <Clipboard size={14} aria-hidden="true" />
                </button>
              </div>
            )}
            {shares.isLoading ? <LoadingState label={copy.loading} /> : shares.isError ? <ErrorState error={shares.error} fallback={copy.shares.failed} /> : !shares.data?.length ? <EmptyState label={copy.shares.empty} /> : (
              <div className="share-list">
                {shares.data.map((share) => (
                  <article className="platform-surface share-row" key={share.id}>
                    <div>
                      <StatusBadge status={share.revoked_at ? "revoked" : "active"} />
                      <span>{share.require_authentication ? copy.shares.signInRequired : copy.shares.public}</span>
                    </div>
                    <span>{share.expires_at ? formatDate(share.expires_at, lang) : copy.shares.noExpiry}</span>
                    <strong>{copy.shares.accessCount(formatNumber(share.access_count, lang))}</strong>
                    {!share.revoked_at && (
                      <button type="button" className="platform-icon-button platform-button-danger" onClick={() => revokeShare.mutate({ meetingId, shareId: share.id })} aria-label={copy.shares.revoke}>
                        <X size={14} aria-hidden="true" />
                      </button>
                    )}
                  </article>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {operationError && <ErrorState error={operationError} fallback={copy.operationFailed} />}
    </div>
  );
}
