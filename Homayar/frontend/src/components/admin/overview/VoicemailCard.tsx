import { Link } from "react-router";

import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState, overviewLinkClass } from "@/components/admin/overview/card-state";
import { VOICEMAIL_COPY } from "@/components/voicemail/voicemail-copy";
import { formatDuration, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useVoicemailAnalytics } from "@/lib/voicemail-api";

/**
 * The voicemail analytics card of the admin overview (HOM-252). It reads the
 * same record-scoped API as the inbox, so its numbers are the workspace's (or
 * team's) last 30 days, never computed from another source.
 */
export default function VoicemailCard() {
  const { lang } = useI18n();
  const copy = VOICEMAIL_COPY[lang];
  const query = useVoicemailAnalytics();
  const state = queryCardState(query, (data) => data.total === 0);
  const data = query.data;
  const handled = data?.by_follow_up_state.done ?? 0;
  const urgent = data?.by_priority.urgent ?? 0;
  const pairs = data
    ? [
        { label: copy.cardTotal, value: data.total },
        { label: copy.cardHandled, value: handled },
        { label: copy.cardOpen, value: Math.max(0, data.total - handled) },
        { label: copy.cardUrgent, value: urgent },
      ]
    : [];

  return (
    <OverviewCard
      title={copy.cardTitle}
      state={state}
      error={query.error}
      emptyText={copy.cardEmpty}
      footer={
        <Link className={overviewLinkClass} to="/voicemails">
          {copy.cardOpenInbox}
        </Link>
      }
    >
      {data && (
        <>
          <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-3">
            {pairs.map((pair) => (
              <div key={pair.label} className="min-w-0">
                <dt className="text-sm text-muted">{pair.label}</dt>
                <dd className="m-0 text-2xl font-semibold tabular-nums">
                  {formatNumber(pair.value, lang)}
                </dd>
              </div>
            ))}
          </dl>
          {data.median_follow_up_seconds !== null && (
            <p className="mb-0 mt-3 text-sm text-muted">
              {copy.cardMedian}:{" "}
              <span className="tabular-nums">
                {formatDuration(data.median_follow_up_seconds, lang)}
              </span>
            </p>
          )}
        </>
      )}
    </OverviewCard>
  );
}
