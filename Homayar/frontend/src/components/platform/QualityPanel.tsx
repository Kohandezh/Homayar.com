import { useI18n } from "@/lib/i18n-context";
import type { MeetingQuality } from "@/types/platform";
import { StatusBadge } from "@/components/platform/Ui";
import { PROCESSING_COPY } from "@/components/platform/processing-copy";
import { RESULTS_COPY } from "@/components/platform/results-copy";

/** Quality, coverage and degradation as the API reports them — never inferred client-side. */
export function QualityPanel({ quality, lang: langProp }: { quality: MeetingQuality; lang?: "fa" | "en" }) {
  const { lang: contextLang } = useI18n();
  const lang = langProp ?? contextLang;
  const copy = RESULTS_COPY[lang].quality;
  const processing = PROCESSING_COPY[lang];
  const coverage =
    quality.speech_coverage_ratio === null
      ? null
      : new Intl.NumberFormat(lang === "fa" ? "fa-IR" : "en", { style: "percent" }).format(quality.speech_coverage_ratio);
  return (
    <section className="meeting-quality" aria-label={copy.title}>
      <div className="meeting-facts">
        {(["transcription", "speaker_attribution", "analysis", "evidence"] as const).map((key) => (
          <div key={key}>
            <span>{copy.labels[key]}</span>
            <StatusBadge status={quality[key]} />
          </div>
        ))}
      </div>
      {quality.analysis_stale && (
        <p className="meeting-stage-runs-note" role="status">{copy.stale}</p>
      )}
      {(coverage !== null || quality.is_mock || quality.degradations.length > 0) && (
        <ul className="meeting-quality-notes">
          {coverage !== null && <li>{copy.coverage(coverage)}</li>}
          {quality.degradations.map((item) => (
            <li key={`${item.stage}-${item.state}`}>
              {processing.stages[item.stage] ?? item.stage}: <StatusBadge status={item.state} />
              {item.reason ? <> {processing.reasons[item.reason] ?? <bdi dir="ltr">{item.reason}</bdi>}</> : null}
            </li>
          ))}
          {quality.is_mock && <li className="meeting-stage-mock">{copy.mock}</li>}
        </ul>
      )}
    </section>
  );
}
