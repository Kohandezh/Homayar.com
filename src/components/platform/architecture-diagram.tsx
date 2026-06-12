const LAYERS = [
  {
    tag: "L1",
    label: "Sources",
    tone: "neutral",
    chips: ["Telephony & contact center", "CRM & APIs", "File upload"],
  },
  {
    tag: "L2",
    label: "Ingestion & transcription",
    tone: "accent",
    chips: ["Batch import", "Scheduled sync", "Speaker-separated transcripts"],
  },
  {
    tag: "L3",
    label: "AI agent layer",
    tone: "violet",
    chips: [
      "Quality",
      "Compliance",
      "Issues",
      "Sentiment",
      "Coaching",
      "Trends",
    ],
  },
  {
    tag: "L4",
    label: "Insights",
    tone: "teal",
    chips: ["Dashboards", "Alerts", "Reports", "API & exports"],
  },
] as const;

const TONE_STYLES = {
  neutral: {
    frame: "border-line bg-raised",
    tag: "border-line-strong text-ink-muted",
    chip: "border-line bg-surface text-ink-soft",
  },
  accent: {
    frame: "border-accent/30 bg-accent/5",
    tag: "border-accent/40 text-accent",
    chip: "border-accent/25 bg-surface text-ink-soft",
  },
  violet: {
    frame: "border-violet/30 bg-violet/5",
    tag: "border-violet/40 text-violet-soft",
    chip: "border-violet/25 bg-surface text-ink-soft",
  },
  teal: {
    frame: "border-teal/30 bg-teal/5",
    tag: "border-teal/40 text-teal",
    chip: "border-teal/25 bg-surface text-ink-soft",
  },
} as const;

function Connector() {
  return (
    <div aria-hidden className="flex justify-center">
      <div className="h-7 border-l border-dashed border-line-strong" />
    </div>
  );
}

export function ArchitectureDiagram() {
  return (
    <div
      role="img"
      aria-label="Layered architecture diagram: call sources flow into ingestion and transcription, then through the AI agent layer, and out as insights — dashboards, alerts, reports, and API exports."
      className="card-surface rounded-2xl p-5 sm:p-8"
    >
      {LAYERS.map((layer, i) => {
        const tone = TONE_STYLES[layer.tone];
        return (
          <div key={layer.tag}>
            {i > 0 && <Connector />}
            <div className={`rounded-xl border p-4 sm:p-5 ${tone.frame}`}>
              <div className="mb-3 flex items-center gap-3">
                <span
                  className={`inline-flex rounded border px-1.5 py-0.5 font-mono text-[11px] ${tone.tag}`}
                >
                  {layer.tag}
                </span>
                <span className="text-sm font-semibold text-ink">
                  {layer.label}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {layer.chips.map((chip) => (
                  <span
                    key={chip}
                    className={`inline-flex rounded-md border px-2.5 py-1 text-xs ${tone.chip}`}
                  >
                    {chip}
                  </span>
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
