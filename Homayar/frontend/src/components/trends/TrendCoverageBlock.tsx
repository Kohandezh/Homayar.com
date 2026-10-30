import { computedValue } from "@/components/trends/trend-format";
import { TRENDS_COPY } from "@/components/trends/trends-copy";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { CoverageCounter, TrendCoverage } from "@/types/organizations";
import type { TrendPopulation } from "@/types/trends";

function CounterValue({ counter }: { counter: CoverageCounter }) {
  const { lang } = useI18n();
  if (counter.status !== "computed" || counter.numerator === null || counter.ratio === null) {
    return <span className="text-muted">{TRENDS_COPY[lang].notAvailable}</span>;
  }
  return <span>{computedValue(counter.numerator, counter.denominator, counter.ratio, lang)}</span>;
}

/** The coverage of the trend window (HOM-81) and the population it counts. */
export default function TrendCoverageBlock({
  coverage,
  population,
}: {
  coverage: TrendCoverage;
  population: TrendPopulation;
}) {
  const { lang } = useI18n();
  const copy = TRENDS_COPY[lang];
  const items = [
    { label: copy.processingCoverage, counter: coverage.processing },
    { label: copy.evaluationCoverage, counter: coverage.evaluation },
  ];

  return (
    <section aria-label={copy.coverageLabel} className="grid gap-2">
      <dl className="m-0 flex flex-wrap gap-x-8 gap-y-3">
        {items.map(({ label, counter }) => (
          <div key={label} className="grid gap-0.5">
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="m-0">
              <CounterValue counter={counter} />
            </dd>
          </div>
        ))}
      </dl>
      <p className="m-0 text-sm text-muted">{copy.callsCounted(formatNumber(population.included, lang))}</p>
    </section>
  );
}
