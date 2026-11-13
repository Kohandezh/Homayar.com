import { Fragment, useId, useRef, useState, type KeyboardEvent } from "react";
import { useI18n } from "@/lib/i18n-context";
import { formatDuration, formatNumber } from "@/lib/format";
import { useCan } from "@/lib/use-can";
import { useTranscriptTimeline } from "@/lib/platform-api";
import type { SpeakerTimeline as TimelineData, TimelineLane, TimelineTurn } from "@/types/timeline";
import { TIMELINE_COPY } from "@/components/platform/timeline-copy";

/** A lane's name: the stored display name, the key as sent, or the unknown word (ADR 0069 D4). */
function laneLabel(lane: TimelineLane, unknownSpeaker: string): string {
  if (lane.kind === "unknown") return unknownSpeaker;
  return lane.display_name ?? lane.speaker_key;
}

/** A body that is not a timeline (a proxy page, a wrong route) shows the error state, never a crash. */
function isSpeakerTimeline(value: unknown): value is TimelineData {
  const timeline = value as Partial<TimelineData> | null | undefined;
  return Boolean(
    timeline &&
    Array.isArray(timeline.lanes) &&
    Array.isArray(timeline.turns) &&
    Array.isArray(timeline.overlaps) &&
    typeof timeline.timeline_end_ms === "number" &&
    timeline.correction &&
    typeof timeline.correction === "object",
  );
}

/**
 * Tick marks across the track: round steps from 0 plus the end mark. Every
 * mark stays inside 0..endMs, and a step mark too close to the end mark is
 * dropped so the two never overlap.
 */
function tickMarks(endMs: number): number[] {
  const steps = [1_000, 2_000, 5_000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 900_000, 1_800_000, 3_600_000];
  const step = steps.find((candidate) => endMs / candidate <= 8) ?? Math.ceil(endMs / 8);
  const marks: number[] = [];
  for (let at = 0; at < endMs; at += step) marks.push(at);
  const kept = marks.filter((at) => at === 0 || endMs - at >= step / 2);
  return kept.concat(endMs);
}

const turnKey = (laneKey: string, segmentId: string) => `${laneKey}\u0000${segmentId}`;

/**
 * Who speaks when (HOM-264): one lane per speaker, the turns as bars on a
 * left-to-right time axis, cross-speaker overlap bands above, and the turns
 * that need a person below. Read only: activating a turn reports to the page,
 * which opens the transcript and cues the audio; the timeline itself never
 * plays and never writes ?at=.
 */
export default function SpeakerTimeline({
  meetingId,
  playbackMs = null,
  onOpenTurn,
  onCorrectTurn,
}: {
  meetingId: string;
  /** Where the audio is, in milliseconds; null until the person plays or seeks. */
  playbackMs?: number | null;
  /** Open the transcript on the segment and cue the audio to its start. */
  onOpenTurn: (segmentId: string, startMs: number) => void;
  /** Open the transcript's existing inline correction form for the segment. */
  onCorrectTurn: (segmentId: string) => void;
}) {
  const { lang } = useI18n();
  const copy = TIMELINE_COPY[lang];
  const can = useCan();
  const headingId = useId();
  const reviewHeadingId = useId();
  const timeline = useTranscriptTimeline(meetingId);
  const turnButtons = useRef(new Map<string, HTMLButtonElement>());
  // The lane and turn that last held focus keep the lane's one tab stop (E6).
  // By segment id, so a refetch that leaves the lane with fewer turns still
  // leaves exactly one tab stop.
  const [roving, setRoving] = useState<{ lane: string; segmentId: string } | null>(null);

  const raw = timeline.data;
  const data = isSpeakerTimeline(raw) ? raw : null;
  const endMs = data?.timeline_end_ms ?? 0;
  const empty =
    data === null || data.revision === null || data.turns.length === 0 || endMs <= 0;
  // A failed background refetch keeps the loaded answer on screen (the page's
  // player follows the same rule); a 200 body that is not a timeline is an
  // error, never a crash.
  const failed = (timeline.isError && raw === undefined) || (raw !== undefined && data === null);

  const labelOf = (lane: TimelineLane) => laneLabel(lane, copy.unknownSpeaker);
  const duration = (ms: number) => formatDuration(ms / 1000, lang);
  const turnName = (label: string, turn: TimelineTurn) => {
    const name = copy.turnName(label, duration(turn.start_ms), duration(turn.end_ms));
    const reasons = turn.review_reasons.map((reason) => copy.reasons[reason]).join(copy.reasonJoin);
    return reasons ? name + copy.needsReview(reasons) : name;
  };
  const bandStyle = (startMs: number, stopMs: number) => ({
    left: `${(startMs / endMs) * 100}%`,
    width: `${((stopMs - startMs) / endMs) * 100}%`,
  });

  /** Arrow keys, Home and End walk one lane's turns in time order; the track is always LTR (E6). */
  function laneKeyDown(event: KeyboardEvent<HTMLElement>, laneKey: string, turns: TimelineTurn[]) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft" && event.key !== "Home" && event.key !== "End") return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const active =
      event.target instanceof Element ? event.target.getAttribute("data-turn-key") : null;
    const current = Math.max(
      0,
      turns.findIndex((turn) => turnKey(laneKey, turn.segment_id) === active),
    );
    let target: number;
    if (event.key === "Home") target = 0;
    else if (event.key === "End") target = turns.length - 1;
    else if (event.key === "ArrowRight") target = Math.min(current + 1, turns.length - 1);
    else target = Math.max(current - 1, 0);
    event.preventDefault();
    if (target === current) return;
    setRoving({ lane: laneKey, segmentId: turns[target].segment_id });
    turnButtons.current.get(turnKey(laneKey, turns[target].segment_id))?.focus();
  }

  if (timeline.isLoading) {
    return (
      <section className="speaker-timeline platform-surface" aria-labelledby={headingId}>
        <h2 id={headingId}>{copy.title}</h2>
        <p className="speaker-timeline-note" role="status">{copy.loading}</p>
      </section>
    );
  }
  if (failed) {
    return (
      <section className="speaker-timeline platform-surface" aria-labelledby={headingId}>
        <h2 id={headingId}>{copy.title}</h2>
        <p className="speaker-timeline-note" role="status">{copy.failed}</p>
      </section>
    );
  }
  if (empty) {
    return (
      <section className="speaker-timeline platform-surface" aria-labelledby={headingId}>
        <h2 id={headingId}>{copy.title}</h2>
        <p className="speaker-timeline-note">{copy.empty}</p>
      </section>
    );
  }

  // Turns of each lane in time order (E6: the arrows follow time, not the
  // API's sequence); a turn whose speaker has no lane is not drawn (E1).
  const turnsByLane = new Map<string, TimelineTurn[]>(data.lanes.map((lane) => [lane.speaker_key, []]));
  for (const turn of data.turns) turnsByLane.get(turn.speaker_key)?.push(turn);
  for (const turns of turnsByLane.values()) {
    turns.sort((first, second) => first.start_ms - second.start_ms || first.sequence - second.sequence);
  }
  const crossOverlaps = data.overlaps.filter((overlap) => overlap.kind === "cross_speaker");
  const reviewTurns = data.turns.filter((turn) => turn.review_reasons.length > 0);
  const canCorrect = data.correction.correctable && can.updateMeeting;
  const laneName = (key: string) => {
    const lane = data.lanes.find((item) => item.speaker_key === key);
    return lane ? labelOf(lane) : key;
  };
  const ticks = tickMarks(endMs);

  return (
    <section className="speaker-timeline platform-surface" aria-labelledby={headingId}>
      <h2 id={headingId}>{copy.title}</h2>
      <p className="speaker-timeline-summary">
        <span>{copy.overlapTotal(formatDuration(data.overlap_ms / 1000, lang))}</span>
        <span>{copy.unknownSpeechTotal(formatDuration(data.unknown_speech_ms / 1000, lang))}</span>
        <span>{copy.overlapBasis}</span>
        {data.revision?.is_mock && <span>{copy.demoData}</span>}
      </p>
      {/* The scroller starts at time 0 even on the RTL page, so its scroll
          origin is the left, like the LTR track inside it (E4). */}
      <div className="speaker-timeline-scroll" dir="ltr">
        {/* Time 0 on the left for both languages, like the native audio bar (E4). */}
        <div className="speaker-timeline-chart" dir="ltr">
          {crossOverlaps.length > 0 && (
            <ul className="speaker-timeline-overlaps" aria-label={copy.overlapsList}>
              {crossOverlaps.map((overlap) => (
                <li
                  key={overlap.index}
                  aria-label={copy.overlapItem(
                    duration(overlap.start_ms),
                    duration(overlap.end_ms),
                    overlap.speaker_keys.map(laneName).join(copy.labelJoin),
                  )}
                  style={bandStyle(overlap.start_ms, overlap.end_ms)}
                >
                  <span className="speaker-timeline-overlap-band" />
                </li>
              ))}
            </ul>
          )}
          <div className="speaker-timeline-lanes">
            {data.lanes.map((lane) => {
              const turns = turnsByLane.get(lane.speaker_key) ?? [];
              const label = labelOf(lane);
              // The lane's one tab stop: the last focused turn while it is
              // still in the lane, else the first turn in time order.
              const rovingId =
                roving?.lane === lane.speaker_key && turns.some((turn) => turn.segment_id === roving.segmentId)
                  ? roving.segmentId
                  : null;
              const tabStopId = rovingId ?? turns[0]?.segment_id ?? null;
              return (
                <div
                  key={lane.speaker_key}
                  role="group"
                  aria-label={label}
                  className={`speaker-timeline-lane${lane.kind === "unknown" ? " is-unknown" : ""}`}
                  onKeyDown={(event) => laneKeyDown(event, lane.speaker_key, turns)}
                >
                  <div className="speaker-timeline-lane-head" dir={lang === "fa" ? "rtl" : "ltr"}>
                    <strong dir="auto">{label}</strong>
                    <span className="speaker-timeline-lane-kind">{copy.kinds[lane.kind]}</span>
                    <bdi dir="ltr">{formatDuration(lane.speech_ms / 1000, lang)}</bdi>
                    <span className="speaker-timeline-lane-kind">
                      {copy.turnCount(formatNumber(lane.turn_count, lang))}
                    </span>
                  </div>
                  <div className="speaker-timeline-track">
                    {turns.map((turn) => (
                      <button
                        key={turn.segment_id}
                        type="button"
                        ref={(element) => {
                          const key = turnKey(lane.speaker_key, turn.segment_id);
                          if (element) turnButtons.current.set(key, element);
                          else turnButtons.current.delete(key);
                        }}
                        data-turn-key={turnKey(lane.speaker_key, turn.segment_id)}
                        className={`speaker-timeline-turn${turn.review_reasons.length > 0 ? " needs-review" : ""}`}
                        aria-label={turnName(label, turn)}
                        tabIndex={turn.segment_id === tabStopId ? 0 : -1}
                        style={{
                          ...bandStyle(turn.start_ms, turn.end_ms),
                          minWidth: "3px",
                        }}
                        onFocus={() => setRoving({ lane: lane.speaker_key, segmentId: turn.segment_id })}
                        onClick={() => onOpenTurn(turn.segment_id, turn.start_ms)}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="speaker-timeline-axis" aria-hidden="true">
            {ticks.map((at, index) => (
              <span
                key={at}
                className={
                  index === 0 ? "is-first" : index === ticks.length - 1 ? "is-last" : undefined
                }
                style={{ left: `${(at / endMs) * 100}%` }}
              >
                <time dir="ltr">{formatDuration(at / 1000, lang)}</time>
              </span>
            ))}
          </div>
          {playbackMs !== null && (
            <div
              className="speaker-timeline-position"
              aria-hidden="true"
              style={{ left: `${Math.min(Math.max((playbackMs / endMs) * 100, 0), 100)}%` }}
            />
          )}
        </div>
      </div>
      {reviewTurns.length > 0 && (
        <section className="speaker-timeline-review" aria-labelledby={reviewHeadingId}>
          <h3 id={reviewHeadingId}>{copy.reviewHeading}</h3>
          <ul>
            {reviewTurns.map((turn) => (
              <li key={turn.segment_id}>
                <strong dir="auto">{laneName(turn.speaker_key)}</strong>
                <bdi dir="ltr">{duration(turn.start_ms)}</bdi>
                <span className="speaker-timeline-review-reasons">
                  {turn.review_reasons.map((reason, index) => (
                    <Fragment key={reason}>
                      {index > 0 && copy.reasonJoin}
                      <span>{copy.reasons[reason]}</span>
                    </Fragment>
                  ))}
                </span>
                <button
                  type="button"
                  className="platform-link-button"
                  aria-label={copy.goToTurn(duration(turn.start_ms))}
                  onClick={() => onOpenTurn(turn.segment_id, turn.start_ms)}
                >
                  {copy.goToTurn(duration(turn.start_ms))}
                </button>
                {canCorrect && (
                  <button
                    type="button"
                    className="platform-link-button"
                    aria-label={copy.correctTurn(duration(turn.start_ms))}
                    onClick={() => onCorrectTurn(turn.segment_id)}
                  >
                    {copy.correctTurn(duration(turn.start_ms))}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
