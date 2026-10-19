import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Clipboard, Eye, EyeOff, FileDown, History, Link2, Pencil, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { apiErrorInfo } from "@/lib/api-errors";
import { useI18n } from "@/lib/i18n-context";
import { formatDate, formatDuration, formatNumber } from "@/lib/format";
import {
  platformQueryKeys,
  saveDownload,
  useActions,
  useCorrectTranscript,
  useCreateAction,
  useCreateShare,
  useDeleteAction,
  useMeetingExport,
  useOriginalTranscript,
  useRenameSpeaker,
  useRevealValue,
  useRevokeShare,
  useShares,
  useTranscript,
  useTranscriptRevision,
  useUpdateAction,
} from "@/lib/platform-api";
import { useCan } from "@/lib/use-can";
import { statusLabel } from "@/lib/status-labels";
import type {
  ActionPriority,
  ActionStatus,
  MeetingDetail,
  MeetingExportFormat,
  Transcript,
  TranscriptCorrectionEdit,
  TranscriptSegment,
} from "@/types/platform";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "@/components/platform/Ui";
import { EvidenceBadge } from "@/components/platform/CallReport";
import MaskedText from "@/components/platform/MaskedText";
import TranscriptCorrectionForm, { type SegmentCorrection } from "@/components/platform/TranscriptCorrectionForm";
import TranscriptRevisionsDrawer from "@/components/platform/TranscriptRevisionsDrawer";
import { RESULTS_COPY, type ResultsTab } from "@/components/platform/results-copy";

function availableTabs(manageShares: boolean): ResultsTab[] {
  return manageShares ? ["transcript", "actions", "shares", "export"] : ["transcript", "actions", "export"];
}

/** The requested panel, or the first tab when this role has no such tab. */
function resolvePanel(panel: ResultsTab, tabs: ResultsTab[]): ResultsTab {
  return tabs.includes(panel) ? panel : tabs[0];
}

/** A request from the page to show one panel, applied once per `seq`. */
export interface ResultsOpenRequest {
  panel: ResultsTab;
  segmentId: string | null;
  seq: number;
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

/** The segment playing at `atMs`: the one containing it, else the last one started before it. */
function segmentAt(segments: TranscriptSegment[], atMs: number): TranscriptSegment | null {
  let before: TranscriptSegment | null = null;
  for (const segment of segments) {
    if (segment.start_ms <= atMs && atMs < segment.end_ms) return segment;
    if (segment.start_ms <= atMs && (!before || segment.start_ms > before.start_ms)) before = segment;
  }
  return before;
}

/**
 * The advanced panels of a meeting (transcript, action items, share links,
 * export). The call report above them replaces the old summary tab.
 */
export default function MeetingResults({
  meeting,
  atMs = null,
  open = null,
}: {
  meeting: MeetingDetail;
  atMs?: number | null;
  open?: ResultsOpenRequest | null;
}) {
  const { lang } = useI18n();
  const copy = RESULTS_COPY[lang];
  const can = useCan();
  const queryClient = useQueryClient();
  const meetingId = meeting.id;
  const tabs = availableTabs(can.manageShares);
  const [tab, setTab] = useState<ResultsTab>(() => (open ? resolvePanel(open.panel, tabs) : "transcript"));
  const [exportFormat, setExportFormat] = useState<MeetingExportFormat>("txt");
  const [speakerFilter, setSpeakerFilter] = useState("");
  const [focusSegment, setFocusSegment] = useState<string | null>(open?.segmentId ?? null);
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
  const [appliedOpen, setAppliedOpen] = useState<number | null>(open?.seq ?? null);
  // HOM-246: one segment's inline correction, its saved note, the read-only
  // old revision the tab may show, and the history drawer. The saved note
  // follows the turn's `sequence`: a correction creates a new revision whose
  // segments have new ids, so the id would no longer match.
  const [editingSegmentId, setEditingSegmentId] = useState<string | null>(null);
  const [savedRevision, setSavedRevision] = useState<{ sequence: number; revisionNumber: number } | null>(null);
  const [viewingRevisionId, setViewingRevisionId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const transcript = useTranscript(meetingId);
  const oldRevision = useTranscriptRevision(meetingId, viewingRevisionId);
  const original = useOriginalTranscript(originalMode === "on" ? meetingId : null);
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
  const correct = useCorrectTranscript();

  const redacted = transcript.data?.privacy_enforced === true && transcript.data.view === "redacted";
  const showingOriginal = originalMode === "on" && Boolean(original.data);
  const viewingOldRevision = viewingRevisionId !== null;
  const shown: Transcript | undefined = viewingOldRevision
    ? oldRevision.data
    : showingOriginal
      ? original.data
      : transcript.data;
  const redaction = transcript.data?.redaction ?? null;
  const canReveal = redacted && !showingOriginal && !viewingOldRevision && can.readOriginalTranscript && Boolean(redaction?.id && redaction.mapping_retained);
  // Corrections go over the revision the person is looking at: only the
  // current revision of the normal (not audited original) view is editable.
  const baseRevision = transcript.data?.revision ?? null;
  const canEditSegments = can.updateMeeting && !showingOriginal && !viewingOldRevision && baseRevision?.is_current === true;

  // A search result's `?at=` (QA Q-01): open the transcript at the segment
  // playing at that time, once per distinct time (back/forward re-applies).
  if (atMs !== null && atMs !== appliedAt && transcript.data) {
    const target = segmentAt(transcript.data.segments, atMs);
    setAppliedAt(atMs);
    setSpeakerFilter("");
    setTab("transcript");
    setFocusSegment(target?.id ?? null);
    setAtOutcome(target ? "found" : "missing");
    // The `?at=` time is of the current transcript, so the current view opens.
    setViewingRevisionId(null);
  }

  // A review action or "Show in transcript" from the call report.
  if (open && open.seq !== appliedOpen) {
    setAppliedOpen(open.seq);
    setTab(resolvePanel(open.panel, tabs));
    if (open.segmentId) {
      setSpeakerFilter("");
      setFocusSegment(open.segmentId);
      setViewingRevisionId(null);
    }
  }

  // Originals and revealed values never outlive the view that asked for them.
  useEffect(() => {
    return () => {
      queryClient.removeQueries({ queryKey: platformQueryKeys.transcriptOriginal(meetingId) });
    };
  }, [meetingId, queryClient]);

  // Focus the requested segment by id, or the turn a correction was saved for
  // by `sequence`: a correction creates a new revision whose segments have new
  // ids, so the id the editor knew may no longer be on the page.
  useEffect(() => {
    if (tab !== "transcript") return;
    const element =
      (focusSegment ? document.getElementById(`segment-${focusSegment}`) : null)
      ?? (savedRevision ? document.querySelector(`[data-sequence="${savedRevision.sequence}"]`) : null);
    if (!element) return;
    element.scrollIntoView?.({ block: "center" });
    element.focus();
  }, [focusSegment, tab, shown, appliedOpen, savedRevision]);

  // A review action lands on its tab, so keyboard users start there.
  useEffect(() => {
    if (appliedOpen === null || !open || open.seq !== appliedOpen || open.segmentId) return;
    tabRefs.current[resolvePanel(open.panel, availableTabs(can.manageShares))]?.focus();
  }, [appliedOpen, open, can.manageShares]);

  function hideOriginal() {
    setOriginalMode("off");
    queryClient.removeQueries({ queryKey: platformQueryKeys.transcriptOriginal(meetingId) });
  }

  /** The inline edit form never outlives the view it was opened in. */
  function leaveEditMode() {
    setEditingSegmentId(null);
    correct.reset();
  }

  /** An old revision is read only: no edit form, no audited original on top of it. */
  function viewRevision(revisionId: string) {
    setHistoryOpen(false);
    setViewingRevisionId(revisionId);
    leaveEditMode();
    if (originalMode !== "off") hideOriginal();
  }

  function showSegment(segmentId: string) {
    setSpeakerFilter("");
    setTab("transcript");
    setFocusSegment(segmentId);
    setViewingRevisionId(null);
  }

  /** One correction over the base revision (HOM-246); only changed fields are sent. */
  function submitCorrection(segment: TranscriptSegment, correction: SegmentCorrection) {
    if (!baseRevision) return;
    const edit: TranscriptCorrectionEdit = { segment_id: segment.id };
    if (correction.text !== undefined) edit.text = correction.text;
    if (correction.speaker_key !== undefined) edit.speaker_key = correction.speaker_key;
    correct.mutate(
      {
        meetingId,
        input: { base_revision_id: baseRevision.id, reason: correction.reason, edits: [edit] },
      },
      {
        onSuccess: (result) => {
          setEditingSegmentId(null);
          correct.reset();
          setSavedRevision({ sequence: segment.sequence, revisionNumber: result.revision?.revision_number ?? 0 });
        },
      },
    );
  }

  /** The revision_stale way out: drop the form and read the changed transcript. */
  function reloadTranscript() {
    setEditingSegmentId(null);
    correct.reset();
    void queryClient.invalidateQueries({ queryKey: platformQueryKeys.transcript(meetingId), exact: true });
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

  // Each tab states the view of its own response (gap G10), never the
  // transcript's.
  const actionsRedacted = (actions.data ?? []).some((action) => action.view === "redacted");
  const actionsOriginalEnforced = (actions.data ?? []).some(
    (action) => action.view === "original" && action.privacy_enforced === true,
  );
  const originalError = original.isError ? apiErrorInfo(original.error).code : null;
  const revealCode = reveal.isError ? apiErrorInfo(reveal.error).code : null;
  const segments = (shown?.segments ?? []).filter((segment) => !speakerFilter || segment.speaker_key === speakerFilter);
  // The edit is alive only while its segment is on screen: a speaker-filter
  // change or a refetched revision (new segment ids) ends it silently, so a
  // vanished segment can never block every Edit button.
  const editingSegment = editingSegmentId === null
    ? null
    : (segments.find((segment) => segment.id === editingSegmentId) ?? null);
  const operationError =
    meetingExport.error ?? createAction.error ?? updateAction.error ?? deleteAction.error ?? renameSpeaker.error ?? createShare.error ?? revokeShare.error;

  return (
    <div className="meeting-results">
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
      </div>

      <div id={`meeting-panel-${tab}`} role="tabpanel" aria-labelledby={`meeting-tab-${tab}`} tabIndex={0} className="meeting-tab-panel">
        {tab === "transcript" && (
          <div className="meeting-transcript-view">
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
                  {can.readOriginalTranscript && originalMode === "off" && !viewingOldRevision && (
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
                        onClick={() => {
                          setOriginalMode("on");
                          setTab("transcript");
                          // The two read-only views exclude each other.
                          setViewingRevisionId(null);
                          leaveEditMode();
                        }}
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
            {atOutcome && appliedAt !== null && (
              <p className="content-view-note" role="status">
                {atOutcome === "found" ? copy.transcript.atFound(formatDuration(appliedAt / 1000, lang)) : copy.transcript.atMissing}
              </p>
            )}
            {viewingOldRevision && oldRevision.data?.revision && (
              <div className="transcript-revision-banner" role="status">
                <p className="m-0">
                  {copy.revisions.viewingRevision(formatNumber(oldRevision.data.revision.revision_number, lang))}
                </p>
                <button type="button" className="platform-button" onClick={() => setViewingRevisionId(null)}>
                  {copy.revisions.backToCurrent}
                </button>
              </div>
            )}
            {transcript.isLoading || (originalMode === "on" && original.isLoading) || (viewingOldRevision && oldRevision.isLoading) ? (
              <LoadingState label={copy.loading} />
            ) : transcript.isError || (viewingOldRevision && oldRevision.isError) ? (
              <ErrorState
                error={viewingOldRevision ? oldRevision.error : transcript.error}
                fallback={copy.transcript.failed}
              />
            ) : !shown?.segments.length ? (
              <EmptyState label={copy.transcript.empty} />
            ) : (
              <>
                <div className="transcript-toolbar">
                  <label>
                    <span>{copy.transcript.speakerFilter}</span>
                    <select
                      value={speakerFilter}
                      onChange={(event) => {
                        setSpeakerFilter(event.target.value);
                        // Filtering swaps the turns on screen, so an open form
                        // would be editing a turn that is no longer shown.
                        leaveEditMode();
                      }}
                    >
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
                  <button type="button" className="platform-button" onClick={() => setHistoryOpen(true)}>
                    <History size={15} aria-hidden="true" />
                    {copy.revisions.title}
                  </button>
                </div>
                {/* The rename forms edit the current view; an old revision is read only. */}
                {can.updateMeeting && !viewingOldRevision && shown.speakers.length > 0 && (
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
                    <article
                      id={`segment-${segment.id}`}
                      key={segment.id}
                      className="meeting-segment"
                      tabIndex={-1}
                      data-sequence={segment.sequence}
                    >
                      <div>
                        <strong dir="auto">{segment.speaker_name}</strong>
                        <time dir="ltr">{formatDuration(segment.start_ms / 1000, lang)}</time>
                        {canEditSegments && editingSegment === null && (
                          <button
                            type="button"
                            className="platform-icon-button"
                            onClick={() => { setEditingSegmentId(segment.id); setSavedRevision(null); }}
                            aria-label={copy.correction.editTurn(formatDuration(segment.start_ms / 1000, lang))}
                          >
                            <Pencil size={14} aria-hidden="true" />
                          </button>
                        )}
                      </div>
                      {/* The form lives only in the view that allows editing it. */}
                      {canEditSegments && editingSegment?.id === segment.id ? (
                        <TranscriptCorrectionForm
                          segment={segment}
                          speakers={shown.speakers}
                          isPending={correct.isPending}
                          error={correct.error}
                          onSave={(correction) => submitCorrection(segment, correction)}
                          onCancel={() => { setEditingSegmentId(null); correct.reset(); }}
                          onReload={reloadTranscript}
                        />
                      ) : (
                        <>
                          <p dir="auto">
                            <MaskedText
                              text={segment.text}
                              copy={copy}
                              revealed={revealed}
                              onPick={canReveal ? (placeholder) => { reveal.reset(); setRevealTarget({ segmentId: segment.id, placeholder }); } : undefined}
                            />
                          </p>
                          {savedRevision?.sequence === segment.sequence && (
                            <p className="segment-saved-note" role="status">
                              {copy.correction.savedAsRevision(formatNumber(savedRevision.revisionNumber, lang))}
                            </p>
                          )}
                        </>
                      )}
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
                          {action.evidence_status && <EvidenceBadge status={action.evidence_status} lang={lang} />}
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

        {tab === "export" && (
          <div className="meeting-export-view">
            <div className="meeting-export-control platform-surface">
              <label>
                <span>{copy.exportFormat}</span>
                <select value={exportFormat} onChange={(event) => setExportFormat(event.target.value as MeetingExportFormat)}>
                  <option value="txt">TXT</option>
                  <option value="json">JSON</option>
                  <option value="srt">SRT</option>
                  <option value="vtt">VTT</option>
                </select>
              </label>
              <button
                type="button"
                className="platform-button"
                onClick={() => meetingExport.mutate({ meetingId, format: exportFormat }, { onSuccess: saveDownload })}
                disabled={meetingExport.isPending}
              >
                <FileDown size={15} aria-hidden="true" />
                {copy.exportAction}
              </button>
            </div>
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

      <TranscriptRevisionsDrawer
        meetingId={meetingId}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onViewRevision={viewRevision}
      />

      {operationError && <ErrorState error={operationError} fallback={copy.operationFailed} />}
    </div>
  );
}
