import type { Dictionary } from "@/i18n/en";
import { Icon } from "@/components/ui/Icon";

type Flow = Dictionary["deployment"]["flow"];

function Node({ label, icon, tone = "default", compact = false }: { label: string; icon: string; tone?: "default" | "amber" | "muted"; compact?: boolean }) {
  const cls =
    tone === "amber"
      ? "border-amber/50 bg-amber/10 text-ink shadow-[0_0_40px_-10px_rgba(245,158,11,0.6)]"
      : tone === "muted"
        ? "border-border-2 bg-bg-2 text-ink-3 border-dashed"
        : "border-border-2 bg-surface text-ink";
  return (
    <div className={`flex items-center justify-center gap-2.5 rounded-xl border px-3 text-center font-semibold ${compact ? "min-h-[48px] py-2 text-xs" : "min-h-[72px] py-3 text-sm"} ${cls}`}>
      <Icon name={icon} size={compact ? 15 : 18} className={tone === "amber" ? "text-amber-text" : tone === "muted" ? "text-ink-3" : "text-teal"} />
      {label}
    </div>
  );
}

function Connector({ blocked = false, label, vertical = false }: { blocked?: boolean; label?: string; vertical?: boolean }) {
  const line = (
    <svg viewBox="0 0 88 12" className={vertical ? "h-8 w-3 rotate-90" : "h-3 w-full min-w-[64px]"} preserveAspectRatio="none" aria-hidden="true">
      <line x1="2" y1="6" x2="86" y2="6" stroke={blocked ? "#f97316" : "#2dd4bf"} strokeWidth="2" strokeLinecap="round" className={blocked ? "" : "flow-path"} strokeDasharray={blocked ? "3 6" : undefined} vectorEffect="non-scaling-stroke" />
      {blocked ? (
        <g stroke="#f97316" strokeWidth="2" strokeLinecap="round" vectorEffect="non-scaling-stroke">
          <line x1="40" y1="2" x2="48" y2="10" />
          <line x1="48" y1="2" x2="40" y2="10" />
        </g>
      ) : (
        <circle cx="86" cy="6" r="2.5" fill="#2dd4bf" />
      )}
    </svg>
  );
  return (
    <div className={`flex items-center justify-center gap-2 ${vertical ? "flex-row py-1" : "flex-col md:min-w-[88px]"}`}>
      {line}
      {label ? <span className={`mono text-[10px] leading-tight ${blocked ? "text-ember" : "text-ink-3"}`}>{label}</span> : null}
    </div>
  );
}

export function FlowDiagram({ flow, mode, compact = false }: { flow: Flow; mode: "saas" | "self-hosted" | "hybrid"; compact?: boolean }) {
  const third =
    mode === "saas" ? (
      <Node label={flow.models} icon="server" compact={compact} />
    ) : mode === "self-hosted" ? (
      <Node label={flow.internet} icon="globe" tone="muted" compact={compact} />
    ) : (
      <Node label={flow.cloud} icon="cloud" compact={compact} />
    );
  const connectorLabel = mode === "saas" ? flow.region : mode === "self-hosted" ? flow.blocked : flow.optIn;
  const blocked = mode === "self-hosted";

  if (compact) {
    return (
      <div className="flex flex-col" aria-hidden="true">
        <Node label={flow.source} icon="phone" compact />
        <Connector vertical />
        <Node label={flow.app} icon="activity" tone="amber" compact />
        <Connector vertical blocked={blocked} label={connectorLabel} />
        {third}
        {blocked ? <p className="mono mt-2 text-center text-[10px] text-ink-3">{flow.allowlist}</p> : null}
      </div>
    );
  }

  return (
    <div className="grid gap-3 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-center" aria-hidden="true">
      <Node label={flow.source} icon="phone" />
      <Connector />
      <Node label={flow.app} icon="activity" tone="amber" />
      <Connector label={connectorLabel} blocked={blocked} />
      {third}
      {blocked ? <p className="mono text-center text-[11px] text-ink-3 md:col-span-5 md:text-start">{flow.allowlist}</p> : null}
    </div>
  );
}
