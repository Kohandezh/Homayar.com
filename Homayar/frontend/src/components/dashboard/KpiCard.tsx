import type { ReactNode } from "react";
import { TrendingUp, TrendingDown } from "lucide-react";

interface KpiCardProps {
  label: string;
  value: string | number;
  unit?: string;
  trend?: number;
  icon: ReactNode;
  color?: string;
}

export default function KpiCard({ label, value, unit, trend, icon, color = "text-accent" }: KpiCardProps) {
  return (
    <div className="bg-bg-secondary border border-border-subtle rounded-xl p-4 hover:border-border-strong transition-colors">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-text-muted uppercase tracking-wider">{label}</span>
        <div className={`${color}`}>{icon}</div>
      </div>
      <div className="flex items-end gap-2">
        <span className="text-2xl font-bold text-text-primary">{value}</span>
        {unit && <span className="text-sm text-text-muted mb-0.5">{unit}</span>}
      </div>
      {trend !== undefined && (
        <div className={`flex items-center gap-1 mt-2 text-xs ${trend >= 0 ? "text-success" : "text-error"}`}>
          {trend >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
          <span>{Math.abs(trend).toFixed(1)}%</span>
        </div>
      )}
    </div>
  );
}
