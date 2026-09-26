import { useState } from "react";
import { Phone, Upload, Clock, Globe, Loader2 } from "lucide-react";
import { useCalls, useCallDetail, useAnalyzeCall, useUploadCall } from "@/lib/hooks";

export default function CallsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [filterLang, setFilterLang] = useState<string>("");

  const { data: calls = [], isLoading: callsLoading } = useCalls(filterStatus || undefined, filterLang || undefined);
  const { data: selected, isLoading: detailLoading } = useCallDetail(selectedId);
  const analyzeMutation = useAnalyzeCall();
  const uploadMutation = useUploadCall();

  const uploading = uploadMutation.isPending;

  async function uploadFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    uploadMutation.mutate({ file });
  }

  async function analyzeCall(id: string) {
    analyzeMutation.mutate(id, {
      onSuccess: () => setSelectedId(id),
    });
  }

  const statusColor: Record<string, string> = {
    uploaded: "bg-yellow-500/20 text-yellow-400",
    transcribing: "bg-blue-500/20 text-blue-400",
    analyzing: "bg-purple-500/20 text-purple-400",
    completed: "bg-green-500/20 text-green-400",
    failed: "bg-red-500/20 text-red-400",
  };

  const sentimentIcon: Record<string, string> = {
    negative: "😠", neutral: "😐", positive: "😊",
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      {/* Left: Call List */}
      <div className="w-[380px] border-r border-border-subtle flex flex-col">
        {/* Upload + Filters */}
        <div className="p-4 border-b border-border-subtle space-y-3">
          <div className="flex items-center gap-2">
            <label className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 cursor-pointer disabled:opacity-50">
              {uploading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
              {uploading ? "Uploading..." : "Upload Audio"}
              <input type="file" accept=".wav,.mp3,.ogg,.m4a,.flac" onChange={uploadFile} className="hidden" disabled={uploading} />
            </label>
          </div>
          <div className="flex gap-2">
            <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className="flex-1 bg-bg-elevated text-text-primary text-xs rounded-lg px-2 py-1.5 border border-border-subtle">
              <option value="">All Status</option>
              <option value="uploaded">Uploaded</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
            <select value={filterLang} onChange={e => setFilterLang(e.target.value)} className="flex-1 bg-bg-elevated text-text-primary text-xs rounded-lg px-2 py-1.5 border border-border-subtle">
              <option value="">All Languages</option>
              <option value="fa">فارسی</option>
              <option value="en">English</option>
            </select>
          </div>
        </div>

        {/* Call List */}
        <div className="flex-1 overflow-y-auto">
          {callsLoading ? (
            <div className="p-4 text-center text-text-secondary text-sm">Loading...</div>
          ) : calls.length === 0 ? (
            <div className="p-4 text-center text-text-secondary text-sm">No calls found</div>
          ) : (
            calls.map(call => (
              <button
                key={call.id}
                onClick={() => setSelectedId(call.id)}
                className={`w-full text-left px-4 py-3 border-b border-border-subtle hover:bg-bg-elevated transition-colors ${
                  selectedId === call.id ? "bg-accent/5 border-l-2 border-l-accent" : ""
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Phone size={14} className="text-text-secondary" />
                    <span className="text-sm text-text-primary font-medium">{call.agent_name || "Unknown"}</span>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${statusColor[call.status] || "bg-gray-500/20 text-gray-400"}`}>
                    {call.status}
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-1 text-xs text-text-secondary">
                  <span className="flex items-center gap-1"><Clock size={10} />{Math.round(call.duration_seconds)}s</span>
                  <span className="flex items-center gap-1"><Globe size={10} />{call.language === "fa" ? "FA" : "EN"}</span>
                  {call.overall_qc_score != null && (
                    <span className="text-text-primary">{Math.round(call.overall_qc_score * 100)}/100</span>
                  )}
                  {call.sentiment && <span>{sentimentIcon[call.sentiment]}</span>}
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Right: Call Detail */}
      <div className="flex-1 overflow-y-auto p-6">
        {!selectedId || detailLoading || !selected ? (
          !selectedId ? (
            <div className="flex items-center justify-center h-full text-text-secondary">
              <div className="text-center">
                <Phone size={48} className="mx-auto mb-3 opacity-30" />
                <p>Select a call to view details</p>
              </div>
            </div>
          ) : detailLoading ? (
            <div className="flex items-center justify-center h-full text-text-secondary">Loading...</div>
          ) : (
            <div className="flex items-center justify-center h-full text-text-secondary">Call details unavailable</div>
          )
        ) : (
          <div className="max-w-3xl mx-auto space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-text-primary">Call Report</h2>
                <p className="text-sm text-text-secondary">{selected.call?.file_name}</p>
              </div>
              <div className="flex items-center gap-2">
                {selected.call?.status === "uploaded" && (
                  <button
                    onClick={() => analyzeCall(selected.call.id)}
                    disabled={analyzeMutation.isPending}
                    className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 disabled:opacity-50"
                  >
                    {analyzeMutation.isPending ? "Analyzing..." : "Analyze Call"}
                  </button>
                )}
                {selected.qc_report?.is_mock && (
                  <span className="text-xs px-2 py-1 rounded-full bg-yellow-500/20 text-yellow-400">Mock Data</span>
                )}
              </div>
            </div>

            {/* Call Info */}
            <div className="grid grid-cols-4 gap-3">
              {[
                { label: "Agent", value: selected.call?.agent_name },
                { label: "Direction", value: selected.call?.direction },
                { label: "Duration", value: `${Math.round(selected.call?.duration_seconds || 0)}s` },
                { label: "Language", value: selected.call?.language === "fa" ? "فارسی" : "English" },
              ].map(item => (
                <div key={item.label} className="bg-bg-secondary rounded-lg p-3 border border-border-subtle">
                  <p className="text-xs text-text-secondary">{item.label}</p>
                  <p className="text-sm font-medium text-text-primary mt-1">{item.value}</p>
                </div>
              ))}
            </div>

            {/* QC Report */}
            {selected.qc_report && (
              <div className="bg-bg-secondary rounded-lg p-4 border border-border-subtle">
                <h3 className="text-sm font-semibold text-text-primary mb-3">Quality Control</h3>
                <div className="grid grid-cols-4 gap-2">
                  {[
                    { label: "Overall", value: selected.qc_report.overall_score },
                    { label: "Empathy", value: selected.qc_report.empathy },
                    { label: "Problem Solving", value: selected.qc_report.problem_solving },
                    { label: "Clarity", value: selected.qc_report.communication_clarity },
                    { label: "Closing", value: selected.qc_report.call_closing },
                    { label: "Silence", value: selected.qc_report.silence_handling },
                    { label: "Tone", value: selected.qc_report.tone },
                  ].map(m => (
                    <div key={m.label} className="text-center">
                      <p className="text-xs text-text-secondary">{m.label}</p>
                      <p className={`text-lg font-bold ${m.value >= 0.7 ? "text-green-400" : m.value >= 0.5 ? "text-yellow-400" : "text-red-400"}`}>
                        {Math.round(m.value * 100)}
                      </p>
                    </div>
                  ))}
                </div>
                <div className="flex gap-4 mt-3 pt-3 border-t border-border-subtle text-xs text-text-secondary">
                  <span>Sentiment: {selected.qc_report.sentiment}</span>
                  <span>Resolution: {selected.qc_report.resolution}</span>
                  <span>Silence: {selected.qc_report.silence_total_seconds}s</span>
                </div>
              </div>
            )}

            {/* Problems */}
            {selected.problems && (
              <div className="bg-bg-secondary rounded-lg p-4 border border-border-subtle">
                <h3 className="text-sm font-semibold text-text-primary mb-2">Problem Detection</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Root Cause</span>
                    <span className="text-text-primary">{selected.problems.root_cause}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Severity</span>
                    <span className={`${selected.problems.severity === "critical" ? "text-red-400" : selected.problems.severity === "high" ? "text-orange-400" : "text-yellow-400"}`}>
                      {selected.problems.severity}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Category</span>
                    <span className="text-text-primary">{selected.problems.issue_category}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Business Insights */}
            {selected.insights && (
              <div className="bg-bg-secondary rounded-lg p-4 border border-border-subtle">
                <h3 className="text-sm font-semibold text-text-primary mb-2">Business Insight</h3>
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div className="text-center p-2 bg-bg-elevated rounded-lg">
                    <p className="text-xs text-text-secondary">NPS</p>
                    <p className="text-lg font-bold text-text-primary">{selected.insights.estimated_nps}</p>
                  </div>
                  <div className="text-center p-2 bg-bg-elevated rounded-lg">
                    <p className="text-xs text-text-secondary">Churn Risk</p>
                    <p className={`text-lg font-bold ${selected.insights.churn_risk === "high" ? "text-red-400" : selected.insights.churn_risk === "medium" ? "text-yellow-400" : "text-green-400"}`}>
                      {selected.insights.churn_risk}
                    </p>
                  </div>
                  <div className="text-center p-2 bg-bg-elevated rounded-lg">
                    <p className="text-xs text-text-secondary">Upsell</p>
                    <p className="text-lg font-bold text-text-primary">{selected.insights.upsell_signal ? "Yes" : "No"}</p>
                  </div>
                </div>
              </div>
            )}

            {/* Transcript */}
            {selected.transcript && (
              <div className="bg-bg-secondary rounded-lg p-4 border border-border-subtle">
                <h3 className="text-sm font-semibold text-text-primary mb-3">Transcript</h3>
                <div className="space-y-2">
                  {selected.transcript.segments.map((seg, i) => {
                    const parts = seg.split(": ");
                    const speaker = parts[0];
                    const text = parts.slice(1).join(": ");
                    return (
                      <div key={i} className={`flex gap-2 ${speaker === "customer" ? "justify-end" : ""}`}>
                        <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                          speaker === "customer" ? "bg-accent/10 text-text-primary" : "bg-bg-elevated text-text-primary"
                        }`}>
                          <span className="text-xs text-text-secondary block mb-0.5">{speaker}</span>
                          <span dir={selected.call?.language === "fa" ? "rtl" : "ltr"}>{text}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
