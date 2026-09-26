import {
  Phone, Activity, AlertTriangle, Clock, TrendingUp, Users, Star, Zap
} from "lucide-react";
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, ResponsiveContainer,
} from "recharts";
import KpiCard from "./KpiCard";
import { useKpis, useQcTrend, useSentiment, useIssues, useRecentCalls } from "@/lib/hooks";

const SENTIMENT_COLORS: Record<string, string> = {
  positive: "#22C55E",
  neutral: "#FACC15",
  negative: "#EF4444",
};

export default function DashboardPage() {
  const { data: kpis } = useKpis();
  const { data: trend = [] } = useQcTrend();
  const { data: sentiment = [] } = useSentiment();
  const { data: issues = [] } = useIssues();
  const { data: recentCalls = [] } = useRecentCalls();

  const statusColor: Record<string, string> = {
    uploaded: "text-yellow-400", transcribing: "text-blue-400",
    analyzing: "text-purple-400", completed: "text-green-400", failed: "text-red-400",
  };
  const sentimentEmoji: Record<string, string> = { positive: "😊", neutral: "😐", negative: "😠" };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-text-primary">Overview</h1>

      {/* KPI Row */}
      <div className="grid grid-cols-4 gap-4">
        <KpiCard label="Total Calls" value={kpis?.total_calls ?? "--"} icon={<Phone size={16} />} color="text-accent" />
        <KpiCard label="Avg QC Score" value={kpis ? Math.round(kpis.avg_qc_score * 100) : "--"} unit="/ 100" icon={<Activity size={16} />} color="text-success" />
        <KpiCard label="Avg NPS" value={kpis ? Math.round(kpis.avg_nps) : "--"} icon={<Star size={16} />} color="text-warning" />
        <KpiCard label="Open Issues" value={kpis?.open_issues ?? "--"} icon={<AlertTriangle size={16} />} color="text-error" />
      </div>

      {/* Second KPI Row */}
      <div className="grid grid-cols-4 gap-4">
        <KpiCard label="Calls Today" value={kpis?.calls_today ?? "--"} icon={<Clock size={16} />} color="text-accent" />
        <KpiCard label="Avg Duration" value={kpis ? Math.round(kpis.avg_duration_seconds) : "--"} unit="sec" icon={<TrendingUp size={16} />} color="text-info" />
        <KpiCard label="Churn Risk" value={kpis?.churn_risk_calls ?? "--"} icon={<Users size={16} />} color="text-error" />
        <KpiCard label="Upsell Opps" value={kpis?.upsell_opportunities ?? "--"} icon={<Zap size={16} />} color="text-success" />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-12 gap-4">
        {/* QC Trend */}
        <div className="col-span-8 bg-bg-secondary border border-border-subtle rounded-xl p-4">
          <h3 className="text-sm font-semibold text-text-primary mb-4">QC Score Trend</h3>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={trend}>
              <defs>
                <linearGradient id="qcGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#6366F1" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#6366F1" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="timestamp" tickFormatter={v => new Date(v).toLocaleDateString([], { month: "short", day: "numeric" })} stroke="rgba(255,255,255,0.2)" tick={{ fontSize: 10, fill: "rgba(255,255,255,0.45)" }} />
              <YAxis domain={[0, 1]} stroke="rgba(255,255,255,0.2)" tick={{ fontSize: 10, fill: "rgba(255,255,255,0.45)" }} />
              <Tooltip contentStyle={{ background: "#111827", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: 12 }} />
              <Area type="monotone" dataKey="quality" stroke="#6366F1" fill="url(#qcGrad)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Sentiment Distribution */}
        <div className="col-span-4 bg-bg-secondary border border-border-subtle rounded-xl p-4">
          <h3 className="text-sm font-semibold text-text-primary mb-4">Sentiment</h3>
          {sentiment.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={sentiment} dataKey="count" nameKey="sentiment" cx="50%" cy="50%" outerRadius={70} innerRadius={40}>
                  {sentiment.map((entry, i) => (
                    <Cell key={i} fill={SENTIMENT_COLORS[entry.sentiment] || "#666"} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-[200px] text-text-muted text-sm">No data</div>
          )}
          <div className="flex justify-center gap-4 mt-2">
            {sentiment.map(s => (
              <span key={s.sentiment} className="text-xs text-text-secondary">
                {sentimentEmoji[s.sentiment]} {s.sentiment}: {s.count}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Issues + Recent Calls */}
      <div className="grid grid-cols-12 gap-4">
        {/* Issues by Category */}
        <div className="col-span-5 bg-bg-secondary border border-border-subtle rounded-xl p-4">
          <h3 className="text-sm font-semibold text-text-primary mb-4">Issues by Category</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={issues} layout="vertical">
              <XAxis type="number" stroke="rgba(255,255,255,0.2)" tick={{ fontSize: 10, fill: "rgba(255,255,255,0.45)" }} />
              <YAxis type="category" dataKey="category" width={80} stroke="rgba(255,255,255,0.2)" tick={{ fontSize: 11, fill: "rgba(255,255,255,0.7)" }} />
              <Tooltip contentStyle={{ background: "#111827", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontSize: 12 }} />
              <Bar dataKey="critical" stackId="a" fill="#EF4444" />
              <Bar dataKey="count" stackId="a" fill="#FACC15" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Recent Calls */}
        <div className="col-span-7 bg-bg-secondary border border-border-subtle rounded-xl p-4">
          <h3 className="text-sm font-semibold text-text-primary mb-3">Recent Calls</h3>
          <div className="space-y-1 max-h-[240px] overflow-y-auto">
            {recentCalls.length === 0 ? (
              <p className="text-xs text-text-muted py-4 text-center">No calls yet</p>
            ) : recentCalls.map(c => (
              <div key={c.id} className="flex items-center justify-between py-2 px-2 rounded-lg hover:bg-bg-elevated text-sm">
                <div className="flex items-center gap-3">
                  <span className={statusColor[c.status] || "text-text-secondary"}>{c.status === "completed" ? "✓" : "○"}</span>
                  <span className="text-text-primary">{c.agent_name}</span>
                  <span className="text-text-muted text-xs">{c.direction}</span>
                </div>
                <div className="flex items-center gap-3 text-xs text-text-secondary">
                  {c.sentiment && <span>{sentimentEmoji[c.sentiment]}</span>}
                  {c.overall_qc_score != null && <span>{Math.round(c.overall_qc_score * 100)}</span>}
                  <span>{Math.round(c.duration_seconds)}s</span>
                  <span>{c.language === "fa" ? "FA" : "EN"}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
