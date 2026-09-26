import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";

interface AgentStat {
  agent: string;
  calls: number;
  avg_qc: number;
  empathy: number;
  problem_solving: number;
  clarity: number;
}

export default function AnalyticsPage() {
  const { data: categories = [], isLoading: catLoading } = useQuery({
    queryKey: ["dashboard", "issues"],
    queryFn: async () => {
      const res = await api.get("/dashboard/issues");
      return res.data;
    },
  });

  const { data: qcDetails = [], isLoading: qcLoading } = useQuery({
    queryKey: ["analytics", "qc-details"],
    queryFn: async () => {
      const res = await api.get("/dashboard/call-qc-details");
      return res.data;
    },
  });

  // Compute agent stats from bulk QC data (no N+1)
  const agents: AgentStat[] = (() => {
    const agentMap: Record<string, { calls: number; qcScores: number[]; empathyScores: number[]; psScores: number[]; clarityScores: number[] }> = {};
    for (const row of qcDetails) {
      const name = row.agent_name || "Unknown";
      if (!agentMap[name]) agentMap[name] = { calls: 0, qcScores: [], empathyScores: [], psScores: [], clarityScores: [] };
      const a = agentMap[name];
      a.calls++;
      if (row.overall_score != null) a.qcScores.push(row.overall_score);
      if (row.empathy != null) a.empathyScores.push(row.empathy);
      if (row.problem_solving != null) a.psScores.push(row.problem_solving);
      if (row.communication_clarity != null) a.clarityScores.push(row.communication_clarity);
    }

    return Object.entries(agentMap).map(([name, data]) => ({
      agent: name,
      calls: data.calls,
      avg_qc: data.qcScores.length ? Math.round(data.qcScores.reduce((a, b) => a + b, 0) / data.qcScores.length * 100) : 0,
      empathy: data.empathyScores.length ? Math.round(data.empathyScores.reduce((a, b) => a + b, 0) / data.empathyScores.length * 100) : 0,
      problem_solving: data.psScores.length ? Math.round(data.psScores.reduce((a, b) => a + b, 0) / data.psScores.length * 100) : 0,
      clarity: data.clarityScores.length ? Math.round(data.clarityScores.reduce((a, b) => a + b, 0) / data.clarityScores.length * 100) : 0,
    }));
  })();

  if (catLoading || qcLoading) {
    return <div className="flex items-center justify-center h-[calc(100vh-3.5rem)] text-text-secondary">Loading analytics...</div>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-text-primary">Analytics</h1>

      {/* Agent Performance Comparison */}
      <div className="bg-bg-secondary border border-border-subtle rounded-xl p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Agent Performance Comparison</h3>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={agents}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="agent" stroke="rgba(255,255,255,0.2)" tick={{ fontSize: 11, fill: "rgba(255,255,255,0.7)" }} />
            <YAxis domain={[0, 100]} stroke="rgba(255,255,255,0.2)" tick={{ fontSize: 10, fill: "rgba(255,255,255,0.45)" }} />
            <Tooltip contentStyle={{ background: "#111827", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: 12 }} />
            <Bar dataKey="avg_qc" name="Avg QC" fill="#6366F1" radius={[4, 4, 0, 0]} />
            <Bar dataKey="empathy" name="Empathy" fill="#22C55E" radius={[4, 4, 0, 0]} />
            <Bar dataKey="problem_solving" name="Problem Solving" fill="#FACC15" radius={[4, 4, 0, 0]} />
            <Bar dataKey="clarity" name="Clarity" fill="#3B82F6" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* Issues by Category */}
        <div className="col-span-6 bg-bg-secondary border border-border-subtle rounded-xl p-5">
          <h3 className="text-sm font-semibold text-text-primary mb-4">Issues by Category</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={categories}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="category" stroke="rgba(255,255,255,0.2)" tick={{ fontSize: 10, fill: "rgba(255,255,255,0.7)" }} />
              <YAxis stroke="rgba(255,255,255,0.2)" tick={{ fontSize: 10, fill: "rgba(255,255,255,0.45)" }} />
              <Tooltip contentStyle={{ background: "#111827", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: 12 }} />
              <Bar dataKey="count" fill="#6366F1" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Agent Calls Table */}
        <div className="col-span-6 bg-bg-secondary border border-border-subtle rounded-xl p-5">
          <h3 className="text-sm font-semibold text-text-primary mb-4">Agent Summary</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-subtle">
                  <th className="text-left py-2 text-xs text-text-muted font-medium">Agent</th>
                  <th className="text-center py-2 text-xs text-text-muted font-medium">Calls</th>
                  <th className="text-center py-2 text-xs text-text-muted font-medium">Avg QC</th>
                  <th className="text-center py-2 text-xs text-text-muted font-medium">Empathy</th>
                </tr>
              </thead>
              <tbody>
                {agents.sort((a, b) => b.avg_qc - a.avg_qc).map(a => (
                  <tr key={a.agent} className="border-b border-border-subtle hover:bg-bg-elevated">
                    <td className="py-2 text-text-primary">{a.agent}</td>
                    <td className="text-center text-text-secondary">{a.calls}</td>
                    <td className="text-center">
                      <span className={a.avg_qc >= 70 ? "text-green-400" : a.avg_qc >= 50 ? "text-yellow-400" : "text-red-400"}>
                        {a.avg_qc}
                      </span>
                    </td>
                    <td className="text-center text-text-secondary">{a.empathy}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
