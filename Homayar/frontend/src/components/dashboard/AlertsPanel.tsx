import { AlertTriangle, CircleDot, Info } from "lucide-react";
import type { Alert } from "@/types";

const severityConfig: Record<string, { color: string; icon: typeof AlertTriangle }> = {
  critical: { color: "text-error", icon: AlertTriangle },
  warning: { color: "text-warning", icon: AlertTriangle },
  info: { color: "text-info", icon: Info },
};

export default function AlertsPanel({ alerts }: { alerts: Alert[] }) {
  return (
    <div className="bg-bg-secondary border border-border-subtle rounded-xl p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">Recent Alerts</h3>
      <div className="space-y-2 max-h-[320px] overflow-y-auto">
        {alerts.length === 0 && (
          <p className="text-xs text-text-muted py-4 text-center">No alerts</p>
        )}
        {alerts.map((alert) => {
          const config = severityConfig[alert.severity] || severityConfig.info;
          const Icon = config.icon;
          return (
            <div
              key={alert.id}
              className="flex items-start gap-3 p-2 rounded-lg hover:bg-bg-elevated transition-colors cursor-pointer"
            >
              <Icon size={14} className={`${config.color} mt-0.5 shrink-0`} />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-text-primary truncate">{alert.title}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs text-text-muted">{alert.region}</span>
                  <CircleDot size={6} className="text-text-muted" />
                  <span className="text-xs text-text-muted">{alert.status}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
