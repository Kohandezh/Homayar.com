import type { ReactNode } from "react";

import { VOICE_COPY } from "@/components/admin/voice/voice-copy";
import { formatNumber, isolateLtr } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { VoiceMetrics } from "@/types/voice";

/** One term and its value of the metrics list. */
function Pair({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-muted">{term}</dt>
      <dd className="m-0 text-sm tabular-nums">{children}</dd>
    </div>
  );
}

/** One reason map: a line per entry, sorted by reason code; an empty map shows "None". */
function ReasonMap({ map }: { map: Record<string, number> }) {
  const { lang } = useI18n();
  const copy = VOICE_COPY[lang];
  const entries = Object.entries(map).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  if (entries.length === 0) return <>{copy.none}</>;
  return (
    <span className="flex flex-col">
      {entries.map(([code, count]) => (
        // A reason code is Latin text: isolated, it keeps its order inside a Persian line.
        <span key={code}>
          {isolateLtr(code)}: {formatNumber(count, lang)}
        </span>
      ))}
    </span>
  );
}

/**
 * The runtime metrics of the voice control plane: what this API process has
 * counted since it started. Every number goes through formatNumber, so
 * Persian shows Persian digits.
 */
export default function VoiceMetricsPanel({ metrics }: { metrics: VoiceMetrics }) {
  const { lang } = useI18n();
  const copy = VOICE_COPY[lang];
  const latency = metrics.admission_latency_ms;
  return (
    <div className="space-y-3">
      <p className="m-0 text-sm text-muted">{copy.metricsNote}</p>
      <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <Pair term={copy.admittedTotal}>{formatNumber(metrics.admitted_total, lang)}</Pair>
        <Pair term={copy.activeCalls}>{formatNumber(metrics.active_calls, lang)}</Pair>
        <Pair term={copy.rejections}>
          <ReasonMap map={metrics.rejections_by_reason} />
        </Pair>
        <Pair term={copy.releases}>
          <ReasonMap map={metrics.releases_by_reason} />
        </Pair>
        <Pair term={copy.latencyCount}>{formatNumber(latency.count, lang)}</Pair>
        <Pair term={copy.latencyMean}>{copy.ms(formatNumber(latency.mean, lang, 1))}</Pair>
        <Pair term={copy.latencyP50}>{copy.ms(formatNumber(latency.p50, lang, 1))}</Pair>
        <Pair term={copy.latencyP95}>{copy.ms(formatNumber(latency.p95, lang, 1))}</Pair>
        <Pair term={copy.latencyP99}>{copy.ms(formatNumber(latency.p99, lang, 1))}</Pair>
        <Pair term={copy.latencyMax}>{copy.ms(formatNumber(latency.max, lang, 1))}</Pair>
      </dl>
    </div>
  );
}
