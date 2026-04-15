import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { listTrainingRuns, deleteTrainingRun, updateTrainingRun, type TrainingRunStatus } from "../api";
import { FlaskConical, CheckCircle, XCircle, Loader2, Clock, ChevronDown, ChevronRight, Rocket, Download, Trash2, BarChart3, Tag, MessageSquare, Trophy, Timer } from "lucide-react";
import LineChart from "../components/LineChart";

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

function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return "—";
  const start = new Date(startedAt).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const secs = Math.round((end - start) / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

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
  for (const [k, v] of Object.entries(metrics)) {
    if (!result.some((r) => r.key === k)) {
      const short = k.replace("metrics/", "").replace("val/", "val ").replace("(B)", "").replace("(M)", " mask");
      result.push({ label: short, key: k, value: v });
    }
  }
  return result;
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  completed: { bg: "var(--success-soft)", color: "var(--success)" },
  failed: { bg: "var(--danger-soft)", color: "var(--danger)" },
  training: { bg: "var(--accent-soft)", color: "var(--accent)" },
  validating: { bg: "var(--accent-soft)", color: "var(--accent)" },
  queued: { bg: "var(--bg-inset)", color: "var(--text-muted)" },
  preparing: { bg: "var(--bg-inset)", color: "var(--text-muted)" },
};

const RUN_COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b"];

type SortKey = "newest" | "oldest" | "best_map" | "status";

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

  const sorted = preFiltered.length > 0 ? [...preFiltered].sort((a, b) => {
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

  if (sortBy === "newest") sorted.reverse();

  const failedCount = runs ? runs.filter((r) => r.status === "failed").length : 0;
  const completedRuns = (runs || []).filter((r) => r.status === "completed");

  // Find best run by mAP50
  const bestRun = completedRuns.length > 0
    ? completedRuns.reduce((best, r) => {
        const bMap = metricValue(best, "mAP50(B)") ?? metricValue(best, "mAP50") ?? -1;
        const rMap = metricValue(r, "mAP50(B)") ?? metricValue(r, "mAP50") ?? -1;
        return rMap > bMap ? r : best;
      })
    : null;
  const bestRunId = bestRun?.run_id;
  const bestMaP = bestRun ? (metricValue(bestRun, "mAP50(B)") ?? metricValue(bestRun, "mAP50")) : null;

  // Total training time
  const totalTrainSecs = completedRuns.reduce((sum, r) => {
    if (r.started_at && r.completed_at) {
      return sum + (new Date(r.completed_at).getTime() - new Date(r.started_at).getTime()) / 1000;
    }
    return sum;
  }, 0);

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

  // Build overlaid comparison chart data
  const comparisonChartData = compared.length >= 2 ? (() => {
    const maxEpochs = Math.max(...compared.map((r) => r.loss_history?.length || 0));
    if (maxEpochs < 2) return null;

    const lossSeries: { key: string; label: string; color: string }[] = [];
    const metricSeries: { key: string; label: string; color: string }[] = [];
    const mergedLoss: Record<string, number>[] = [];
    const mergedMetric: Record<string, number>[] = [];

    for (let i = 0; i < maxEpochs; i++) {
      const lossRow: Record<string, number> = { epoch: i + 1 };
      const metricRow: Record<string, number> = { epoch: i + 1 };

      compared.forEach((r, ri) => {
        const lh = r.loss_history || [];
        const mh = r.metric_history || [];
        const name = r.name || r.model_variant;

        if (i < lh.length) {
          for (const [k, v] of Object.entries(lh[i])) {
            if (k.includes("val") && k.includes("box_loss")) {
              const seriesKey = `loss_${ri}`;
              lossRow[seriesKey] = v;
              if (i === 0) lossSeries.push({ key: seriesKey, label: name, color: RUN_COLORS[ri % RUN_COLORS.length] });
            }
          }
        }
        if (i < mh.length) {
          for (const [k, v] of Object.entries(mh[i])) {
            if (k.includes("mAP50") && k.includes("(B)")) {
              const seriesKey = `map_${ri}`;
              metricRow[seriesKey] = v;
              if (i === 0) metricSeries.push({ key: seriesKey, label: name, color: RUN_COLORS[ri % RUN_COLORS.length] });
            }
          }
        }
      });

      mergedLoss.push(lossRow);
      mergedMetric.push(metricRow);
    }

    return { lossSeries, metricSeries, mergedLoss, mergedMetric };
  })() : null;

  // Hyperparameter diff for comparison
  const hpDiff = compared.length >= 2 ? (() => {
    const allHpKeys = new Set<string>();
    compared.forEach((r) => Object.keys(r.hyperparameters || {}).forEach((k) => allHpKeys.add(k)));
    const diffs: { key: string; values: (string | number | null)[] }[] = [];
    for (const key of allHpKeys) {
      const values = compared.map((r) => (r.hyperparameters || {})[key] ?? null);
      const stringVals = values.map((v) => v == null ? null : String(v));
      const hasVariation = new Set(stringVals.filter((v) => v != null)).size > 1;
      if (hasVariation) {
        diffs.push({ key, values: values as (string | number | null)[] });
      }
    }
    return diffs;
  })() : [];

  return (
    <div className="min-h-screen">

      <div className="max-w-6xl mx-auto mt-8 px-4">
        <div className="flex items-center justify-between mb-6">
          <div>
            <p className="eyebrow" style={{ marginBottom: 4 }}>Experiment tracking</p>
            <h1 className="text-2xl font-bold flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
              <FlaskConical size={24} />
              Experiments
            </h1>
          </div>
          <div className="flex items-center gap-2">
            {failedCount > 0 && (
              <label className="flex items-center gap-1.5 text-xs cursor-pointer mr-3" style={{ color: "var(--text-muted)" }}>
                <input type="checkbox" checked={hideFailed} onChange={() => setHideFailed(!hideFailed)} className="rounded" />
                Hide failed ({failedCount})
              </label>
            )}
            <span className="eyebrow" style={{ fontSize: 10 }}>Sort</span>
            {([
              { key: "newest" as SortKey, label: "Newest" },
              { key: "best_map" as SortKey, label: "Best mAP" },
              { key: "status" as SortKey, label: "Status" },
            ]).map((s) => (
              <button
                key={s.key}
                onClick={() => setSortBy(s.key)}
                className="px-2.5 py-1 rounded text-xs"
                style={sortBy === s.key
                  ? { backgroundColor: "var(--text-primary)", color: "var(--bg-page)" }
                  : { backgroundColor: "var(--bg-inset)", color: "var(--text-secondary)" }
                }
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* Summary dashboard */}
        {completedRuns.length > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            <div className="surface p-3" style={{ borderRadius: "var(--radius-md)" }}>
              <span className="eyebrow block">Best mAP50</span>
              <span style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-serif)", color: "var(--success)" }}>
                {formatPct(bestMaP)}
              </span>
              {bestRun && (
                <span className="block truncate" style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 2 }}>
                  {bestRun.name || bestRun.model_variant}
                </span>
              )}
            </div>
            <div className="surface p-3" style={{ borderRadius: "var(--radius-md)" }}>
              <span className="eyebrow block">Completed</span>
              <span style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-serif)", color: "var(--text-primary)" }}>
                {completedRuns.length}
              </span>
              <span className="block" style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 2 }}>
                of {(runs || []).length} total runs
              </span>
            </div>
            <div className="surface p-3" style={{ borderRadius: "var(--radius-md)" }}>
              <span className="eyebrow block">Total Epochs</span>
              <span style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-serif)", color: "var(--text-primary)" }}>
                {completedRuns.reduce((s, r) => s + r.epoch_current, 0)}
              </span>
              <span className="block" style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 2 }}>
                across all runs
              </span>
            </div>
            <div className="surface p-3" style={{ borderRadius: "var(--radius-md)" }}>
              <span className="eyebrow block">Training Time</span>
              <span style={{ fontSize: 24, fontWeight: 700, fontFamily: "var(--font-serif)", color: "var(--text-primary)" }}>
                {totalTrainSecs < 3600 ? `${Math.round(totalTrainSecs / 60)}m` : `${(totalTrainSecs / 3600).toFixed(1)}h`}
              </span>
              <span className="block" style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 2 }}>
                compute time
              </span>
            </div>
          </div>
        )}

        {/* Comparison panel */}
        {compared.length >= 2 && (
          <div className="surface p-5 mb-6" style={{ borderRadius: "var(--radius-lg)" }}>
            <div className="flex items-center justify-between mb-4">
              <p className="eyebrow flex items-center gap-1.5">
                <BarChart3 size={12} />
                Comparing {compared.length} runs
              </p>
              <button onClick={() => setCompareIds(new Set())} className="text-xs hover:underline" style={{ color: "var(--accent)" }}>Clear</button>
            </div>

            {/* Hyperparameter diff — what changed between runs */}
            {hpDiff.length > 0 && (
              <div className="mb-4 p-3 rounded-lg" style={{ backgroundColor: "var(--bg-inset)", border: "1px solid var(--border-subtle)" }}>
                <p className="eyebrow mb-2">What Changed</p>
                <div className="grid gap-1">
                  {hpDiff.map((d) => (
                    <div key={d.key} className="flex items-center gap-3 text-xs">
                      <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", minWidth: 80 }}>{d.key}</span>
                      <div className="flex gap-2">
                        {d.values.map((v, i) => (
                          <span
                            key={i}
                            className="px-1.5 py-0.5 rounded"
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: 11,
                              backgroundColor: `${RUN_COLORS[i % RUN_COLORS.length]}15`,
                              color: RUN_COLORS[i % RUN_COLORS.length],
                              fontWeight: 600,
                            }}
                          >
                            {v == null ? "—" : String(v)}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Overlaid charts */}
            {comparisonChartData && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
                {comparisonChartData.lossSeries.length > 0 && (
                  <div>
                    <p className="eyebrow mb-1">Val Box Loss</p>
                    <LineChart data={comparisonChartData.mergedLoss} series={comparisonChartData.lossSeries} height={140} />
                  </div>
                )}
                {comparisonChartData.metricSeries.length > 0 && (
                  <div>
                    <p className="eyebrow mb-1">mAP50</p>
                    <LineChart data={comparisonChartData.mergedMetric} series={comparisonChartData.metricSeries} height={140} yMin={0} yMax={1} />
                  </div>
                )}
              </div>
            )}

            {/* Metrics table with deltas */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <th className="text-left py-1.5 pr-4 eyebrow" style={{ fontSize: 10 }}>Metric</th>
                    {compared.map((r, i) => (
                      <th key={r.run_id} className="text-right py-1.5 px-3" style={{ fontSize: 11 }}>
                        <div style={{ color: RUN_COLORS[i % RUN_COLORS.length], fontWeight: 600 }}>{r.name || r.model_variant}</div>
                        <div style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 10 }}>
                          {r.epoch_current} ep · {formatDuration(r.started_at, r.completed_at)}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Array.from(allMetricKeys).sort().filter((k) => !k.includes("sem_loss") && !k.includes("fitness")).map((key) => {
                    const values = compared.map((r) => (r.best_metrics || r.metrics || {})[key]);
                    const nums = values.filter((v) => v != null).map(Number);
                    const isLoss = key.includes("loss");
                    const best = nums.length > 0 ? (isLoss ? Math.min(...nums) : Math.max(...nums)) : null;
                    return (
                      <tr key={key} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                        <td className="py-1 pr-4" style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                          {key.replace("metrics/", "").replace("val/", "")}
                        </td>
                        {compared.map((r, i) => {
                          const v = values[i];
                          const isBest = v != null && best != null && Number(v) === best && nums.length > 1;
                          // Delta vs first run
                          const baseVal = values[0];
                          const delta = (v != null && baseVal != null && i > 0) ? Number(v) - Number(baseVal) : null;
                          const deltaGood = delta != null ? (isLoss ? delta < 0 : delta > 0) : false;
                          return (
                            <td key={r.run_id} className="text-right px-3" style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontVariantNumeric: "tabular-nums" }}>
                              <span style={{ color: isBest ? "var(--success)" : "var(--text-secondary)", fontWeight: isBest ? 700 : 400 }}>
                                {v != null ? Number(v).toFixed(4) : "—"}
                              </span>
                              {delta != null && Math.abs(delta) > 0.0001 && (
                                <span style={{ fontSize: 9, marginLeft: 4, color: deltaGood ? "var(--success)" : "var(--danger)" }}>
                                  {deltaGood ? "+" : ""}{(delta * (key.includes("loss") ? 1 : 100)).toFixed(1)}{key.includes("loss") ? "" : "%"}
                                </span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {isLoading && <p style={{ color: "var(--text-muted)" }}>Loading experiments...</p>}

        {/* Run list */}
        <div className="space-y-2">
          {sorted.map((run) => {
            const statusStyle = STATUS_COLORS[run.status] || STATUS_COLORS.queued;
            const StatusIcon = run.status === "completed" ? CheckCircle
              : run.status === "failed" ? XCircle
              : ["training", "validating"].includes(run.status) ? Loader2
              : Clock;
            const isSpinning = ["training", "validating"].includes(run.status);
            const isSelected = compareIds.has(run.run_id);
            const isExpanded = expandedId === run.run_id;
            const isBest = run.run_id === bestRunId;
            const mAP = metricValue(run, "mAP50(B)") ?? metricValue(run, "mAP50");
            const precision = metricValue(run, "precision(B)") ?? metricValue(run, "precision");
            const recall = metricValue(run, "recall(B)") ?? metricValue(run, "recall");
            const allMetrics = run.best_metrics || run.metrics || {};
            const categorized = categorizeMetrics(allMetrics);
            const duration = formatDuration(run.started_at, run.completed_at);

            return (
              <div
                key={run.run_id}
                className="surface overflow-hidden"
                style={{
                  borderRadius: "var(--radius-md)",
                  borderColor: isBest ? "var(--success)" : isSelected ? "var(--accent)" : undefined,
                  backgroundColor: isSelected ? "var(--accent-soft)" : undefined,
                }}
              >
                {/* Row header */}
                <div
                  className="flex items-center gap-3 p-4 cursor-pointer transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : run.run_id)}
                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-surface-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={(e) => { e.stopPropagation(); toggleCompare(run.run_id); }}
                    disabled={!isSelected && compareIds.size >= 4}
                    className="rounded"
                  />

                  {isExpanded
                    ? <ChevronDown size={14} style={{ color: "var(--text-muted)" }} />
                    : <ChevronRight size={14} style={{ color: "var(--text-muted)" }} />
                  }

                  <StatusIcon size={16} style={{ color: statusStyle.color }} className={isSpinning ? "animate-spin" : ""} />

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      {isBest && <Trophy size={13} style={{ color: "var(--warning)" }} />}
                      <span className="font-medium" style={{ color: "var(--text-primary)", fontFamily: "var(--font-serif)" }}>
                        {run.name || `Run ${run.run_id.slice(0, 8)}`}
                      </span>
                      <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{run.model_variant}</span>
                      <span
                        style={{
                          fontSize: 10, padding: "1px 6px", borderRadius: 4,
                          backgroundColor: statusStyle.bg, color: statusStyle.color,
                          fontFamily: "var(--font-mono)",
                        }}
                      >
                        {run.status}
                      </span>
                      {run.tags && run.tags.length > 0 && run.tags.map((tag) => (
                        <span
                          key={tag}
                          style={{
                            fontSize: 10, padding: "1px 5px", borderRadius: 4,
                            backgroundColor: "var(--accent-soft)", color: "var(--accent)",
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                    <p style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                      {run.epoch_current}/{run.total_epochs} epochs
                      {duration !== "—" && <> · <Timer size={10} className="inline" style={{ verticalAlign: "-1px" }} /> {duration}</>}
                    </p>
                  </div>

                  <div className="flex gap-4 text-xs shrink-0">
                    {mAP != null && (
                      <div className="text-center">
                        <span className="eyebrow block" style={{ fontSize: 9 }}>mAP50</span>
                        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: isBest ? "var(--success)" : "var(--text-primary)", fontVariantNumeric: "tabular-nums", fontSize: 13 }}>
                          {formatPct(mAP)}
                        </span>
                      </div>
                    )}
                    {precision != null && (
                      <div className="text-center">
                        <span className="eyebrow block" style={{ fontSize: 9 }}>Prec</span>
                        <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{formatPct(precision)}</span>
                      </div>
                    )}
                    {recall != null && (
                      <div className="text-center">
                        <span className="eyebrow block" style={{ fontSize: 9 }}>Recall</span>
                        <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{formatPct(recall)}</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Expanded detail panel */}
                {isExpanded && (
                  <div className="px-5 pb-5 pt-4" style={{ borderTop: "1px solid var(--border-subtle)", backgroundColor: "var(--bg-inset)" }}>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                      {/* Config */}
                      <div>
                        <p className="eyebrow mb-3">Configuration</p>
                        <div className="space-y-2 text-sm">
                          {[
                            ["Model", run.model_variant],
                            ["Task", run.task_type],
                            ["Epochs", `${run.epoch_current} / ${run.total_epochs}`],
                            ["Duration", duration],
                            ["Batch", run.hyperparameters?.batch ?? "—"],
                            ["Img Size", run.hyperparameters?.imgsz ?? "—"],
                            ["Augment", run.hyperparameters?.augmentation ?? "—"],
                            ["Patience", run.hyperparameters?.patience ?? "—"],
                          ].map((pair) => { const label = String(pair[0]); const value = pair[1]; return (
                            <div key={label} className="flex justify-between items-center">
                              <span style={{ color: "var(--text-secondary)" }}>{label}</span>
                              <span style={{ fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text-primary)" }}>{String(value)}</span>
                            </div>
                          ); })}
                          {run.error_message && (
                            <div className="mt-2 text-xs rounded p-2" style={{ backgroundColor: "var(--danger-soft)", color: "var(--danger)", border: "1px solid var(--danger)" }}>
                              {run.error_message.slice(0, 200)}
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Best metrics */}
                      <div>
                        <p className="eyebrow mb-3">Best Metrics</p>
                        {categorized.length > 0 ? (
                          <div className="grid grid-cols-2 gap-2">
                            {categorized.slice(0, 12).map((m) => (
                              <div key={m.key} className="rounded-lg p-2" style={{ backgroundColor: "var(--bg-surface)" }}>
                                <span className="block mb-0.5" style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{m.label}</span>
                                <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
                                  {m.label.includes("loss") ? m.value.toFixed(4) : formatPct(m.value)}
                                </span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs" style={{ color: "var(--text-muted)" }}>No metrics recorded</p>
                        )}
                      </div>
                    </div>

                    {/* Tags + Notes */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 mt-4 pt-3" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                      <div>
                        <p className="eyebrow mb-2 flex items-center gap-1"><Tag size={10} /> Tags</p>
                        <div className="flex flex-wrap gap-1.5">
                          {(run.tags || []).map((tag) => (
                            <span
                              key={tag}
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded"
                              style={{ fontSize: 11, fontFamily: "var(--font-mono)", backgroundColor: "var(--accent-soft)", color: "var(--accent)" }}
                            >
                              {tag}
                              <button
                                onClick={async (e) => {
                                  e.stopPropagation();
                                  await updateTrainingRun(run.run_id, { tags: (run.tags || []).filter((t) => t !== tag) });
                                  queryClient.invalidateQueries({ queryKey: ["training-runs"] });
                                }}
                                style={{ color: "var(--accent)", fontSize: 10, border: "none", background: "none" }}
                              >
                                &times;
                              </button>
                            </span>
                          ))}
                          <button
                            onClick={async (e) => {
                              e.stopPropagation();
                              const tag = prompt("Add tag:");
                              if (tag?.trim()) {
                                await updateTrainingRun(run.run_id, { tags: [...(run.tags || []), tag.trim()] });
                                queryClient.invalidateQueries({ queryKey: ["training-runs"] });
                              }
                            }}
                            className="px-2 py-0.5 rounded"
                            style={{ fontSize: 11, border: "1px dashed var(--border-default)", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}
                          >
                            + tag
                          </button>
                        </div>
                      </div>
                      <div>
                        <p className="eyebrow mb-2 flex items-center gap-1"><MessageSquare size={10} /> Notes</p>
                        <textarea
                          defaultValue={run.notes || ""}
                          placeholder="Add notes about this run..."
                          rows={2}
                          onBlur={async (e) => {
                            const val = e.target.value.trim();
                            if (val !== (run.notes || "")) {
                              await updateTrainingRun(run.run_id, { notes: val });
                              queryClient.invalidateQueries({ queryKey: ["training-runs"] });
                            }
                          }}
                          className="w-full px-2 py-1.5 rounded-lg text-xs"
                          style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)", fontFamily: "var(--font-mono)", resize: "vertical", outline: "none" }}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex gap-2 mt-4 pt-3" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                      <Link
                        to={`/train/${run.run_id}`}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg text-white"
                        style={{ backgroundColor: "var(--accent)" }}
                      >
                        {run.status === "training" ? "Monitor" : "View Curves"}
                      </Link>
                      {run.status === "completed" && (
                        <Link
                          to="/deploy/models"
                          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg text-white"
                          style={{ backgroundColor: "var(--success)" }}
                        >
                          <Rocket size={12} />
                          Deploy
                        </Link>
                      )}
                      {run.weights_url && (
                        <a
                          href={run.weights_url}
                          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg"
                          style={{ border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}
                        >
                          <Download size={12} />
                          Weights
                        </a>
                      )}
                      {run.status !== "training" && (
                        <button
                          onClick={() => handleDelete(run.run_id)}
                          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg ml-auto"
                          style={{ color: "var(--danger)", border: "1px solid var(--danger)" }}
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
              <FlaskConical size={48} className="mx-auto mb-4" style={{ color: "var(--text-muted)" }} />
              <p className="text-lg mb-2" style={{ color: "var(--text-primary)" }}>No experiments yet</p>
              <p className="text-sm mb-4" style={{ color: "var(--text-muted)" }}>
                Train a model from a dataset to start tracking experiments.
              </p>
              <Link to="/datasets" className="px-4 py-2 text-white rounded-lg text-sm inline-block" style={{ backgroundColor: "var(--accent)" }}>
                Go to Datasets
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
