import { useState } from "react";
import { AlertTriangle, AlertCircle, Info } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";

type IncidentSeverity = "critical" | "high" | "medium" | "low";

interface Incident {
  root_cause: string;
  issue_category: string;
  severity: IncidentSeverity;
  system_failures: string[];
  repeated_issue: boolean;
  impact_scope: string | null;
}

interface CallIncident {
  call_id: string;
  agent_name: string;
  created_at: string;
  language: string;
  incident: Incident;
}

export default function IncidentsPage() {
  const [filterSev, setFilterSev] = useState<string>("");

  const { data: incidents = [], isLoading } = useQuery<CallIncident[]>({
    queryKey: ["incidents"],
    queryFn: async () => {
      const res = await api.get("/dashboard/call-problems");
      return res.data;
    },
  });

  const filtered = filterSev ? incidents.filter(i => i.incident.severity === filterSev) : incidents;
  const severities: IncidentSeverity[] = ["critical", "high", "medium", "low"];
  const sevOrder: Record<IncidentSeverity, number> = { critical: 0, high: 1, medium: 2, low: 3 };
  filtered.sort((a, b) => (sevOrder[a.incident.severity] ?? 4) - (sevOrder[b.incident.severity] ?? 4));

  const sevConfig: Record<IncidentSeverity, { color: string; bg: string; icon: typeof AlertTriangle }> = {
    critical: { color: "text-red-400", bg: "bg-red-500/10 border-red-500/30", icon: AlertTriangle },
    high: { color: "text-orange-400", bg: "bg-orange-500/10 border-orange-500/30", icon: AlertCircle },
    medium: { color: "text-yellow-400", bg: "bg-yellow-500/10 border-yellow-500/30", icon: Info },
    low: { color: "text-blue-400", bg: "bg-blue-500/10 border-blue-500/30", icon: Info },
  };

  const counts: Record<IncidentSeverity, number> = { critical: 0, high: 0, medium: 0, low: 0 };
  incidents.forEach((item) => { counts[item.incident.severity] += 1; });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text-primary">Quality Incidents</h1>
        <div className="flex gap-2">
          {severities.map((sev) => {
            const count = counts[sev];
            const cfg = sevConfig[sev];
            return (
              <button
                key={sev}
                onClick={() => setFilterSev(filterSev === sev ? "" : sev)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                  filterSev === sev ? cfg.bg + " " + cfg.color : "bg-bg-secondary border-border-subtle text-text-secondary"
                }`}
              >
                {count} {sev}
              </button>
            );
          })}
        </div>
      </div>

      {isLoading ? (
        <div className="text-center text-text-secondary py-12">Loading incidents...</div>
      ) : filtered.length === 0 ? (
        <div className="text-center text-text-secondary py-12">No incidents found</div>
      ) : (
        <div className="space-y-3">
          {filtered.map(item => {
            const cfg = sevConfig[item.incident.severity] || sevConfig.low;
            const Icon = cfg.icon;
            return (
              <div key={item.call_id} className={`rounded-xl border p-4 ${cfg.bg}`}>
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-3">
                    <Icon size={18} className={`${cfg.color} mt-0.5 shrink-0`} />
                    <div>
                      <p className="text-sm font-medium text-text-primary" dir={item.language === "fa" ? "rtl" : "ltr"}>
                        {item.incident.root_cause}
                      </p>
                      <div className="flex items-center gap-3 mt-1 text-xs text-text-secondary">
                        <span>{item.agent_name}</span>
                        <span>{item.incident.issue_category}</span>
                        {item.incident.impact_scope && <span>Impact: {item.incident.impact_scope}</span>}
                      </div>
                      {item.incident.system_failures?.length > 0 && (
                        <div className="flex gap-1 mt-2">
                          {item.incident.system_failures.map((f, i) => (
                            <span key={i} className="text-xs px-2 py-0.5 rounded-full bg-red-500/20 text-red-400">{f}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {item.incident.repeated_issue && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-500/20 text-yellow-400">Repeated</span>
                    )}
                    <span className={`text-xs font-medium uppercase ${cfg.color}`}>{item.incident.severity}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
