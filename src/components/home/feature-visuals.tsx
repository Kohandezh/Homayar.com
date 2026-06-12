import { CheckIcon, CategoryIcon } from "@/components/icons";

/**
 * Abstract, purely geometric CSS/SVG visuals for the landing page feature
 * sections. Server components — no interactivity, no external images.
 * All visuals are decorative and hidden from assistive technology.
 */

const WAVE_HEIGHTS = [
  34, 58, 42, 72, 50, 88, 64, 96, 70, 82, 54, 90, 62, 76, 44, 68, 38, 56, 30,
  48,
];

function Waveform({ className = "" }: { className?: string }) {
  return (
    <div className={`flex h-12 items-end gap-1 ${className}`}>
      {WAVE_HEIGHTS.map((h, i) => (
        <span
          key={i}
          className={`w-1 flex-1 rounded-full ${
            i % 3 === 0 ? "bg-accent/80" : i % 3 === 1 ? "bg-accent/40" : "bg-violet/50"
          }`}
          style={{ height: `${h}%` }}
        />
      ))}
    </div>
  );
}

/* 1 ── Full-coverage QA: 2% sample vs 100% coverage */
export function CoverageVisual() {
  return (
    <div aria-hidden className="card-surface p-6 sm:p-8">
      <div className="space-y-6">
        <div>
          <div className="flex items-baseline justify-between">
            <p className="text-sm text-ink-muted">Manual QA sample</p>
            <p className="font-mono text-sm text-ink-muted">2%</p>
          </div>
          <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-overlay">
            <div className="h-full w-[2%] min-w-1.5 rounded-full bg-ink-faint" />
          </div>
        </div>
        <div>
          <div className="flex items-baseline justify-between">
            <p className="text-sm font-medium text-ink">Homayar coverage</p>
            <p className="font-mono text-sm font-semibold text-accent">100%</p>
          </div>
          <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-overlay">
            <div className="h-full w-full rounded-full bg-gradient-to-r from-accent to-violet" />
          </div>
        </div>
        <div className="grid grid-cols-10 gap-1.5 pt-2">
          {Array.from({ length: 40 }).map((_, i) => (
            <span
              key={i}
              className={`aspect-square rounded-sm ${
                i === 17 ? "bg-amber/80" : "bg-accent/30"
              }`}
            />
          ))}
        </div>
        <p className="font-mono text-xs text-ink-faint">
          every call scored · evidence linked · zero sampling bias
        </p>
      </div>
    </div>
  );
}

/* 2 ── Agentic analysis: seven specialists on one call */
const AGENTS: { name: string; dot: string }[] = [
  { name: "Transcription", dot: "bg-accent" },
  { name: "Scoring", dot: "bg-teal" },
  { name: "Compliance", dot: "bg-amber" },
  { name: "Issue detection", dot: "bg-danger" },
  { name: "Sentiment", dot: "bg-violet-soft" },
  { name: "Coaching", dot: "bg-success" },
  { name: "Trend analysis", dot: "bg-accent" },
];

export function AgentsVisual() {
  return (
    <div aria-hidden className="card-surface p-6 sm:p-8">
      <div className="flex items-center justify-between">
        <p className="font-mono text-xs text-ink-faint">call · inbound · 06:42</p>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-0.5 font-mono text-xs text-accent">
          <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-accent" />
          analyzing
        </span>
      </div>
      <Waveform className="mt-4" />
      <div className="mt-6 flex flex-wrap gap-2">
        {AGENTS.map((agent) => (
          <span
            key={agent.name}
            className="inline-flex items-center gap-2 rounded-full border border-line bg-raised px-3 py-1.5 text-xs font-medium text-ink-soft"
          >
            <span className={`h-1.5 w-1.5 rounded-full ${agent.dot}`} />
            {agent.name}
          </span>
        ))}
      </div>
      <p className="mt-5 border-t border-line pt-4 font-mono text-xs text-ink-faint">
        7 agents · cross-checked findings · transcript-cited
      </p>
    </div>
  );
}

/* 3 ── Coaching: score ring + strength / growth chips */
export function CoachingVisual() {
  const circumference = 2 * Math.PI * 52;
  const score = 0.87;
  return (
    <div aria-hidden className="card-surface p-6 sm:p-8">
      <div className="flex flex-col items-center gap-6 sm:flex-row sm:gap-8">
        <div className="relative shrink-0">
          <svg width="132" height="132" viewBox="0 0 120 120" className="-rotate-90">
            <circle
              cx="60"
              cy="60"
              r="52"
              fill="none"
              stroke="var(--color-overlay)"
              strokeWidth="9"
            />
            <circle
              cx="60"
              cy="60"
              r="52"
              fill="none"
              stroke="var(--color-accent)"
              strokeWidth="9"
              strokeLinecap="round"
              strokeDasharray={`${circumference * score} ${circumference}`}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="font-mono text-3xl font-semibold text-ink">87</span>
            <span className="font-mono text-[10px] uppercase tracking-wider text-ink-faint">
              QA score
            </span>
          </div>
        </div>
        <div className="w-full space-y-3">
          <div className="rounded-xl border border-line bg-raised p-3.5">
            <p className="font-mono text-[10px] uppercase tracking-wider text-success">
              Strength
            </p>
            <p className="mt-1 text-sm text-ink-soft">
              Clear ownership language during escalations
            </p>
          </div>
          <div className="rounded-xl border border-line bg-raised p-3.5">
            <p className="font-mono text-[10px] uppercase tracking-wider text-amber">
              Growth area
            </p>
            <p className="mt-1 text-sm text-ink-soft">
              Confirm resolution before closing — see 04:12
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* 4 ── Compliance: checklist with one same-day escalation */
const COMPLIANCE_ROWS: { label: string; state: "pass" | "flag" }[] = [
  { label: "Recording disclosure delivered", state: "pass" },
  { label: "Identity verification completed", state: "pass" },
  { label: "Required fee disclosure", state: "flag" },
  { label: "No prohibited language detected", state: "pass" },
];

export function ComplianceVisual() {
  return (
    <div aria-hidden className="card-surface p-6 sm:p-8">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-ink">Compliance checklist</p>
        <p className="font-mono text-xs text-ink-faint">audit trail on</p>
      </div>
      <ul className="mt-5 space-y-2.5">
        {COMPLIANCE_ROWS.map((row) => (
          <li
            key={row.label}
            className="flex items-center gap-3 rounded-xl border border-line bg-raised px-3.5 py-3"
          >
            {row.state === "pass" ? (
              <CheckIcon width={16} height={16} className="shrink-0 text-success" />
            ) : (
              <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                <span className="h-2 w-2 rounded-full bg-danger" />
              </span>
            )}
            <span className="text-sm text-ink-soft">{row.label}</span>
            {row.state === "flag" && (
              <span className="ml-auto rounded-full border border-danger/40 bg-danger/10 px-2 py-0.5 font-mono text-[10px] text-danger">
                escalated
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-4 font-mono text-xs text-ink-faint">
        same-day escalation · transcript evidence attached
      </p>
    </div>
  );
}

/* 5 ── Insights: rising topic trend chart */
export function InsightsVisual() {
  const bars = [28, 34, 30, 42, 38, 52, 48, 60, 56, 70, 66, 82];
  return (
    <div aria-hidden className="card-surface p-6 sm:p-8">
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium text-ink">Issue topics · 12 weeks</p>
        <p className="font-mono text-xs text-accent">billing confusion ↑</p>
      </div>
      <div className="mt-6 flex h-36 items-end gap-2">
        {bars.map((h, i) => (
          <div
            key={i}
            className={`flex-1 rounded-t-md ${
              i >= 9 ? "bg-gradient-to-t from-accent/30 to-accent" : "bg-accent/25"
            }`}
            style={{ height: `${h}%` }}
          />
        ))}
      </div>
      <div className="mt-5 grid grid-cols-3 gap-3 border-t border-line pt-4">
        {[
          { k: "Topics tracked", v: "214" },
          { k: "Churn signals", v: "38" },
          { k: "Root causes", v: "12" },
        ].map((s) => (
          <div key={s.k}>
            <p className="font-mono text-lg font-semibold text-ink">{s.v}</p>
            <p className="text-xs text-ink-muted">{s.k}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

/* 6 ── Enterprise: control grid */
const ENTERPRISE_ITEMS: { icon: string; title: string; sub: string }[] = [
  { icon: "shield", title: "RBAC + SSO", sub: "Full audit logging" },
  { icon: "server", title: "Private cloud", sub: "Or on-premises" },
  { icon: "layers", title: "Encryption", sub: "In transit & at rest" },
  { icon: "eye", title: "Retention", sub: "Configurable + redaction" },
];

export function EnterpriseVisual() {
  return (
    <div aria-hidden className="card-surface p-6 sm:p-8">
      <div className="grid grid-cols-2 gap-3">
        {ENTERPRISE_ITEMS.map((item) => (
          <div
            key={item.title}
            className="rounded-xl border border-line bg-raised p-4"
          >
            <CategoryIcon icon={item.icon} width={20} height={20} className="text-accent" />
            <p className="mt-3 text-sm font-medium text-ink">{item.title}</p>
            <p className="mt-0.5 text-xs text-ink-muted">{item.sub}</p>
          </div>
        ))}
      </div>
      <p className="mt-4 font-mono text-xs text-ink-faint">
        built for banking · healthcare · government procurement
      </p>
    </div>
  );
}
