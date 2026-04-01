import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { listTrainingRuns, deleteTrainingRun, type TrainingRunStatus } from "../api";
import Nav from "../components/Nav";
import { FlaskConical, CheckCircle, XCircle, Loader2, Clock, ChevronDown, ChevronRight, Rocket, Download, Trash2 } from "lucide-react";

const STATUS_ICON: Record<string, typeof CheckCircle> = {
  completed: CheckCircle,
  failed: XCircle,
  training: Loader2,
  queued: Clock,
  preparing: Clock,
  validating: Loader2,
};

const STATUS_COLOR: Record<string, string> = {
  completed: "text-green-600",
  failed: "text-red-600",
  training: "text-blue-600 animate-spin",
  queued: "text-gray-400",
  preparing: "text-gray-400",
  validating: "text-blue-600 animate-spin",
};

type SortKey = "newest" | "oldest" | "best_map" | "status";

function metricValue(run: TrainingRunStatus, key: string): number | null {
  const m = run.best_metrics || run.metrics || {};
  for (const k of Object.keys(m)) {
    if (k.toLowerCase().includes(key.toLowerCase())) return m[k];
  }
  return null;
}

function formatPct(v: number | null): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

// Group metrics into meaningful categories
function categorizeMetrics(metrics: Record<string, number>): { label: string; key: string; value: number }[] {
  const result: { label: string; key: string; value: number }[] = [];
  const order = ["precision", "recall", "mAP50", "mAP50-95", "box_loss", "seg_loss", "cls_loss", "dfl_loss"];

  for (const target of order) {
    for (const [k, v] of Object.entries(metrics)) {
      if (k.includes(target) && k.includes("(B)")) {
        result.push({ label: target, key: k, value: v });
        break;
      }
    }
  }
  // Add any remaining keys not in order
  for (const [k, v] of Object.entries(metrics)) {
    if (!result.some((r) => r.key === k)) {
      const short = k.replace("metrics/", "").replace("val/", "val ").replace("(B)", "").replace("(M)", " mask");
      result.push({ label: short, key: k, value: v });
    }
  }
  return result;
}

export default function ExperimentsPage() {
  const [sortBy, setSortBy] = useState<SortKey>("newest");
  const [compareIds, setCompareIds] = useState<Set<string>>(new Set());
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [hideFailed, setHideFailed] = useState(true);
  const queryClient = useQueryClient();

  const handleDelete = async (runId: string) => {
    if (!confirm("Delete this experiment and its model? This cannot be undone.")) return;
    try {
      await deleteTrainingRun(runId);
      queryClient.invalidateQueries({ queryKey: ["training-runs"] });
      if (expandedId === runId) setExpandedId(null);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const { data: runs, isLoading } = useQuery({
    queryKey: ["training-runs"],
    queryFn: listTrainingRuns,
    refetchInterval: 10000,
  });

  const preFiltered = runs ? (hideFailed ? runs.filter((r) => r.status !== "failed") : runs) : [];

  // API returns oldest-first, so we need to reverse for "newest"
  const sorted = preFiltered.length > 0 ? [...preFiltered].sort((a, b) => {
    if (sortBy === "newest") return 0; // Will reverse below
    if (sortBy === "oldest") return 0; // Keep API order
    if (sortBy === "best_map") {
      const aMap = metricValue(a, "mAP50(B)") ?? metricValue(a, "mAP50") ?? -1;
      const bMap = metricValue(b, "mAP50(B)") ?? metricValue(b, "mAP50") ?? -1;
      return bMap - aMap;
    }
    if (sortBy === "status") {
      const order = ["training", "validating", "queued", "preparing", "completed", "failed"];
      return order.indexOf(a.status) - order.indexOf(b.status);
    }
    return 0;
  }) : [];

  // Reverse for newest-first (API returns oldest first)
  if (sortBy === "newest") sorted.reverse();

  const failedCount = runs ? runs.filter((r) => r.status === "failed").length : 0;

  const toggleCompare = (id: string) => {
    setCompareIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < 4) next.add(id);
      return next;
    });
  };

  const compared = sorted.filter((r) => compareIds.has(r.run_id));

  const allMetricKeys = new Set<string>();
  compared.forEach((r) => {
    Object.keys(r.best_metrics || r.metrics || {}).forEach((k) => allMetricKeys.add(k));
  });

  return (
    <div className="min-h-screen">
      <Nav />
      <div className="max-w-6xl mx-auto mt-8 px-4">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
              <FlaskConical size={24} />
              Experiments
            </h1>
            <p className="text-gray-500 text-sm mt-1">
              {runs?.length || 0} training runs. Select up to 4 to compare. Click a row to expand details.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {failedCount > 0 && (
              <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer mr-3">
                <input type="checkbox" checked={hideFailed} onChange={() => setHideFailed(!hideFailed)} className="rounded" />
                Hide failed ({failedCount})
              </label>
            )}
            <span className="text-xs text-gray-500">Sort:</span>
            {([
              { key: "newest" as SortKey, label: "Newest" },
              { key: "oldest" as SortKey, label: "Oldest" },
              { key: "best_map" as SortKey, label: "Best mAP" },
              { key: "status" as SortKey, label: "Status" },
            ]).map((s) => (
              <button
                key={s.key}
                onClick={() => setSortBy(s.key)}
                className={`px-2.5 py-1 rounded text-xs ${
                  sortBy === s.key ? "bg-gray-900 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* Comparison table */}
        {compared.length >= 2 && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6 overflow-x-auto">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-gray-900">Comparison ({compared.length} runs)</h3>
              <button onClick={() => setCompareIds(new Set())} className="text-xs text-blue-600 hover:underline">Clear</button>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-blue-200">
                  <th className="text-left py-1.5 pr-4 text-gray-500 font-medium">Metric</th>
                  {compared.map((r) => (
                    <th key={r.run_id} className="text-right py-1.5 px-3 font-medium text-xs">
                      <div>{r.name || r.model_variant}</div>
                      <div className="font-normal text-gray-400">{r.epoch_current} epochs</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from(allMetricKeys).sort().filter((k) => !k.includes("sem_loss") && !k.includes("fitness")).map((key) => {
                  const values = compared.map((r) => (r.best_metrics || r.metrics || {})[key]);
                  const nums = values.filter((v) => v != null).map(Number);
                  const isLoss = key.includes("loss");
                  const best = isLoss ? Math.min(...nums) : Math.max(...nums);
                  return (
                    <tr key={key} className="border-t border-blue-100">
                      <td className="py-1 pr-4 text-gray-500 text-xs font-mono">{key.replace("metrics/", "").replace("val/", "")}</td>
                      {compared.map((r, i) => {
                        const v = values[i];
                        const isBest = v != null && Number(v) === best && nums.length > 1;
                        return (
                          <td key={r.run_id} className={`text-right px-3 font-mono text-xs ${isBest ? "text-green-700 font-bold" : ""}`}>
                            {v != null ? Number(v).toFixed(4) : "—"}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {isLoading && <p className="text-gray-500">Loading experiments...</p>}

        {/* Run list */}
        <div className="space-y-2">
          {sorted.map((run) => {
            const Icon = STATUS_ICON[run.status] || Clock;
            const colorCls = STATUS_COLOR[run.status] || "text-gray-400";
            const isSelected = compareIds.has(run.run_id);
            const isExpanded = expandedId === run.run_id;
            const mAP = metricValue(run, "mAP50(B)") ?? metricValue(run, "mAP50");
            const precision = metricValue(run, "precision(B)") ?? metricValue(run, "precision");
            const recall = metricValue(run, "recall(B)") ?? metricValue(run, "recall");
            const allMetrics = run.best_metrics || run.metrics || {};
            const categorized = categorizeMetrics(allMetrics);

            return (
              <div
                key={run.run_id}
                className={`border rounded-lg transition-colors ${
                  isSelected ? "border-blue-500 bg-blue-50/50" : "border-gray-200"
                }`}
              >
                {/* Row header */}
                <div
                  className="flex items-center gap-3 p-4 cursor-pointer hover:bg-gray-50"
                  onClick={() => setExpandedId(isExpanded ? null : run.run_id)}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={(e) => { e.stopPropagation(); toggleCompare(run.run_id); }}
                    disabled={!isSelected && compareIds.size >= 4}
                    className="rounded"
                  />

                  {isExpanded ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />}

                  <Icon size={16} className={colorCls} />

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900">
                        {run.name || `Run ${run.run_id.slice(0, 8)}`}
                      </span>
                      <span className="text-xs text-gray-400">{run.model_variant}</span>
                      <span className="text-xs px-1.5 py-0.5 bg-gray-100 rounded text-gray-500">{run.task_type}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        run.status === "completed" ? "bg-green-100 text-green-700"
                          : run.status === "failed" ? "bg-red-100 text-red-700"
                            : "bg-blue-100 text-blue-700"
                      }`}>
                        {run.status}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {run.epoch_current}/{run.total_epochs} epochs
                    </p>
                  </div>

                  <div className="flex gap-4 text-xs shrink-0">
                    {mAP != null && (
                      <div className="text-center">
                        <span className="text-gray-400 block">mAP50</span>
                        <span className="font-mono font-medium">{formatPct(mAP)}</span>
                      </div>
                    )}
                    {precision != null && (
                      <div className="text-center">
                        <span className="text-gray-400 block">Prec</span>
                        <span className="font-mono">{formatPct(precision)}</span>
                      </div>
                    )}
                    {recall != null && (
                      <div className="text-center">
                        <span className="text-gray-400 block">Recall</span>
                        <span className="font-mono">{formatPct(recall)}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Expanded detail panel */}
                {isExpanded && (
                  <div className="border-t px-5 pb-5 pt-4" style={{ borderColor: "var(--border-subtle)", backgroundColor: "var(--bg-inset)" }}>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                      {/* Config */}
                      <div>
                        <h4 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: "var(--text-muted)" }}>Configuration</h4>
                        <div className="space-y-2 text-sm">
                          {[
                            ["Model", run.model_variant],
                            ["Task", run.task_type],
                            ["Epochs", `${run.epoch_current} / ${run.total_epochs}`],
                            ["Status", run.status],
                          ].map(([label, value]) => (
                            <div key={label} className="flex justify-between items-center">
                              <span style={{ color: "var(--text-secondary)" }}>{label}</span>
                              <span className="font-mono font-medium" style={{ color: "var(--text-primary)" }}>{value}</span>
                            </div>
                          ))}
                          {run.error_message && (
                            <div className="mt-2 text-xs rounded p-2 bg-red-50 text-red-600 border border-red-200">
                              {run.error_message.slice(0, 200)}
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Best metrics */}
                      <div>
                        <h4 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: "var(--text-muted)" }}>Best Metrics</h4>
                        {categorized.length > 0 ? (
                          <div className="grid grid-cols-2 gap-3">
                            {categorized.slice(0, 12).map((m) => (
                              <div key={m.key} className="rounded-lg p-2" style={{ backgroundColor: "var(--bg-surface)" }}>
                                <span className="text-[10px] block mb-0.5" style={{ color: "var(--text-muted)" }}>{m.label}</span>
                                <span className="font-mono text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                                  {m.label.includes("loss")
                                    ? m.value.toFixed(4)
                                    : formatPct(m.value)}
                                </span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs" style={{ color: "var(--text-muted)" }}>No metrics recorded</p>
                        )}
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex gap-2 mt-4 pt-3" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                      <Link
                        to={`/train/${run.run_id}`}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white text-xs rounded-lg hover:bg-blue-700"
                      >
                        {run.status === "training" ? "Monitor Training" : "View Training Curves"}
                      </Link>
                      {run.status === "completed" && (
                        <Link
                          to="/deploy"
                          className="flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white text-xs rounded-lg hover:bg-green-700"
                        >
                          <Rocket size={12} />
                          Deploy Model
                        </Link>
                      )}
                      {run.weights_url && (
                        <a
                          href={run.weights_url}
                          className="flex items-center gap-1.5 px-3 py-1.5 border text-xs rounded-lg" style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}
                        >
                          <Download size={12} />
                          Weights
                        </a>
                      )}
                      {run.status !== "training" && (
                        <button
                          onClick={() => handleDelete(run.run_id)}
                          className="flex items-center gap-1.5 px-3 py-1.5 border border-red-200 text-red-600 text-xs rounded-lg hover:bg-red-50 ml-auto"
                        >
                          <Trash2 size={12} />
                          Delete
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {!isLoading && sorted.length === 0 && (
            <div className="text-center py-12">
              <p className="text-gray-500 text-lg mb-2">No experiments yet</p>
              <p className="text-gray-400 text-sm mb-4">
                Experiments track every training run so you can compare results and find the best model.
              </p>
              <Link to="/datasets" className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 inline-block">
                Go to Datasets to start training
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
