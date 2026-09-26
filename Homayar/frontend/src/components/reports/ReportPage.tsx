import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Download, FileText, CheckCircle, AlertTriangle, TrendingUp, Lightbulb } from "lucide-react";
import { useCallDetail } from "@/lib/hooks";
import api from "@/lib/api";

function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? "bg-green-500" : pct >= 50 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-text-secondary w-28">{label}</span>
      <div className="flex-1 h-2 bg-bg-elevated rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-medium text-text-primary w-8 text-right">{pct}</span>
    </div>
  );
}

export default function ReportPage() {
  const { callId } = useParams<{ callId: string }>();
  const navigate = useNavigate();
  const { data: report, isLoading: loading } = useCallDetail(callId || null);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-3.5rem)]">
        <div className="text-text-secondary">Loading report...</div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-3.5rem)]">
        <div className="text-text-secondary">Report not found</div>
      </div>
    );
  }

  const { call, qc_report, problems, insights, transcript, recommended_actions } = report;
  const isFa = call?.language === "fa";

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate("../", { relative: "path" })} className="p-2 rounded-lg hover:bg-bg-elevated text-text-secondary">
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-xl font-bold text-text-primary">Call Report</h1>
            <p className="text-sm text-text-secondary">{call?.file_name} — {call?.created_at ? new Date(call.created_at).toLocaleString(isFa ? "fa-IR" : "en-US") : ""}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {report?.qc_report?.is_mock && (
            <span className="text-xs px-2 py-1 rounded-full bg-yellow-500/20 text-yellow-400">Mock Data</span>
          )}
          <button
            onClick={async () => {
              try {
                const res = await api.get(`/calls/${callId}/pdf`, { responseType: "blob" });
                const url = window.URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
                const a = document.createElement("a");
                a.href = url;
                a.download = `homayar-report-${callId}.pdf`;
                a.click();
                window.URL.revokeObjectURL(url);
              } catch {
                alert("Failed to generate PDF");
              }
            }}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-bg-secondary border border-border-subtle text-sm text-text-secondary hover:text-text-primary"
          >
            <Download size={14} /> Export PDF
          </button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-bg-secondary rounded-lg p-4 border border-border-subtle text-center">
          <FileText size={20} className="mx-auto mb-1 text-accent" />
          <p className="text-2xl font-bold text-text-primary">{Math.round(call?.duration_seconds || 0)}s</p>
          <p className="text-xs text-text-secondary">Duration</p>
        </div>
        <div className="bg-bg-secondary rounded-lg p-4 border border-border-subtle text-center">
          <CheckCircle size={20} className={`mx-auto mb-1 ${qc_report?.resolution === "successful" ? "text-green-400" : "text-yellow-400"}`} />
          <p className="text-2xl font-bold text-text-primary capitalize">{qc_report?.resolution || "N/A"}</p>
          <p className="text-xs text-text-secondary">Resolution</p>
        </div>
        <div className="bg-bg-secondary rounded-lg p-4 border border-border-subtle text-center">
          <AlertTriangle size={20} className={`mx-auto mb-1 ${problems?.severity === "critical" ? "text-red-400" : problems?.severity === "high" ? "text-orange-400" : "text-yellow-400"}`} />
          <p className="text-2xl font-bold text-text-primary capitalize">{problems?.severity || "N/A"}</p>
          <p className="text-xs text-text-secondary">Severity</p>
        </div>
        <div className="bg-bg-secondary rounded-lg p-4 border border-border-subtle text-center">
          <TrendingUp size={20} className="mx-auto mb-1 text-accent" />
          <p className="text-2xl font-bold text-text-primary">{insights?.estimated_nps || "N/A"}</p>
          <p className="text-xs text-text-secondary">NPS</p>
        </div>
      </div>

      {/* QC Metrics */}
      {qc_report && (
        <div className="bg-bg-secondary rounded-lg p-5 border border-border-subtle">
          <h2 className="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
            <CheckCircle size={16} className="text-accent" /> Quality Control Metrics
          </h2>
          <div className="space-y-3">
            <ScoreBar label="Overall QC" value={qc_report.overall_score} />
            <ScoreBar label="Empathy" value={qc_report.empathy} />
            <ScoreBar label="Problem Solving" value={qc_report.problem_solving} />
            <ScoreBar label="Clarity" value={qc_report.communication_clarity} />
            <ScoreBar label="Call Closing" value={qc_report.call_closing} />
            <ScoreBar label="Silence Handling" value={qc_report.silence_handling} />
            <ScoreBar label="Tone" value={qc_report.tone} />
          </div>
          <div className="flex gap-6 mt-4 pt-4 border-t border-border-subtle text-xs text-text-secondary">
            <span>Sentiment: <b className="text-text-primary">{qc_report.sentiment}</b></span>
            <span>Progression: <b className="text-text-primary">{qc_report.sentiment_progression}</b></span>
            <span>Silence: <b className="text-text-primary">{qc_report.silence_total_seconds}s</b></span>
          </div>
        </div>
      )}

      {/* Root Cause Analysis */}
      {problems && (
        <div className="bg-bg-secondary rounded-lg p-5 border border-border-subtle">
          <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            <AlertTriangle size={16} className="text-yellow-400" /> Root Cause Analysis
          </h2>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between border-b border-border-subtle pb-2">
              <span className="text-text-secondary">Issue</span>
              <span className="text-text-primary font-medium" dir={isFa ? "rtl" : "ltr"}>{problems.root_cause}</span>
            </div>
            <div className="flex justify-between border-b border-border-subtle pb-2">
              <span className="text-text-secondary">Category</span>
              <span className="text-text-primary">{problems.issue_category}</span>
            </div>
            <div className="flex justify-between border-b border-border-subtle pb-2">
              <span className="text-text-secondary">Severity</span>
              <span className={`font-medium ${problems.severity === "critical" ? "text-red-400" : problems.severity === "high" ? "text-orange-400" : "text-yellow-400"}`}>
                {problems.severity}
              </span>
            </div>
            {problems.impact_scope && (
              <div className="flex justify-between border-b border-border-subtle pb-2">
                <span className="text-text-secondary">Impact Scope</span>
                <span className="text-text-primary">{problems.impact_scope}</span>
              </div>
            )}
            {problems.system_failures?.length > 0 && (
              <div className="flex justify-between">
                <span className="text-text-secondary">System Failures</span>
                <span className="text-red-400">{problems.system_failures.join(", ")}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Business Insight */}
      {insights && (
        <div className="bg-bg-secondary rounded-lg p-5 border border-border-subtle">
          <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            <Lightbulb size={16} className="text-yellow-400" /> Business Insight
          </h2>
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: "Upsell Signal", value: insights.upsell_signal ? "Yes" : "No", color: insights.upsell_signal ? "text-green-400" : "text-text-secondary" },
              { label: "Churn Risk", value: insights.churn_risk, color: insights.churn_risk === "high" ? "text-red-400" : insights.churn_risk === "medium" ? "text-yellow-400" : "text-green-400" },
              { label: "NPS", value: insights.estimated_nps?.toString() || "N/A", color: "text-text-primary" },
              { label: "Satisfaction", value: insights.satisfaction_trend, color: insights.satisfaction_trend === "improving" ? "text-green-400" : insights.satisfaction_trend === "declining" ? "text-red-400" : "text-yellow-400" },
              { label: "Customer Intent", value: insights.customer_intent || "N/A", color: "text-text-primary" },
              { label: "Revenue Opportunity", value: insights.revenue_opportunity || "None", color: insights.revenue_opportunity ? "text-green-400" : "text-text-secondary" },
            ].map(item => (
              <div key={item.label} className="flex justify-between py-2 border-b border-border-subtle">
                <span className="text-xs text-text-secondary">{item.label}</span>
                <span className={`text-sm font-medium ${item.color}`} dir={isFa ? "rtl" : "ltr"}>{item.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recommended Actions */}
      {recommended_actions && recommended_actions.length > 0 && (
        <div className="bg-bg-secondary rounded-lg p-5 border border-border-subtle">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Recommended Actions</h2>
          <ul className="space-y-2">
            {recommended_actions.map((action, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-text-primary" dir={isFa ? "rtl" : "ltr"}>
                <span className="text-accent mt-0.5">{i + 1}.</span>
                <span>{action}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Transcript */}
      {transcript && (
        <div className="bg-bg-secondary rounded-lg p-5 border border-border-subtle">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Transcript</h2>
          <div className="space-y-2">
            {transcript.segments.map((seg, i) => {
              const parts = seg.split(": ");
              const speaker = parts[0];
              const text = parts.slice(1).join(": ");
              return (
                <div key={i} className={`flex ${speaker === "customer" ? "justify-end" : ""}`}>
                  <div className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${
                    speaker === "customer" ? "bg-accent/10" : "bg-bg-elevated"
                  }`}>
                    <span className="text-xs text-text-secondary block mb-0.5">{speaker}</span>
                    <span dir={isFa ? "rtl" : "ltr"} className="text-text-primary">{text}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
