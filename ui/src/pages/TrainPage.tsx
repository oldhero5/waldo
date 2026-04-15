import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getJobStatus,
  getTrainingStatus,
  getVariants,
  listModels,
  listTrainingRuns,
  startTraining,
  stopTraining,
  activateModel,
} from "../api";
import TaskSelector from "../components/TaskSelector";
import { Info, AlertTriangle, StopCircle, TrendingDown, Eye, RotateCcw, CheckCircle } from "lucide-react";
import LineChart from "../components/LineChart";
import { humanizeMetricKey, formatMetricValue } from "../lib/metrics";

// Human-readable size labels for model variants
function humanizeVariant(name: string): string {
  if (name.includes("11n")) return `${name} — Nano (fastest, smallest)`;
  if (name.includes("11s")) return `${name} — Small (fast, good accuracy)`;
  if (name.includes("11m")) return `${name} — Medium (balanced)`;
  if (name.includes("11l")) return `${name} — Large (slower, high accuracy)`;
  if (name.includes("11x")) return `${name} — XLarge (slowest, best accuracy)`;
  if (name.includes("26n")) return `${name} — Nano (fastest, smallest)`;
  if (name.includes("26s")) return `${name} — Small (fast, good accuracy)`;
  if (name.includes("26m")) return `${name} — Medium (balanced)`;
  if (name.includes("26l")) return `${name} — Large (slower, high accuracy)`;
  if (name.includes("26x")) return `${name} — XLarge (slowest, best accuracy)`;
  return name;
}

const PRESETS = [
  { label: "Quick", epochs: 50, batch: 16, imgsz: 640 },
  { label: "Standard", epochs: 100, batch: 8, imgsz: 640 },
  { label: "Thorough", epochs: 300, batch: 4, imgsz: 640 },
];

function Tooltip({ text }: { text: string }) {
  return (
    <span className="relative group ml-1 inline-flex" title={text}>
      <Info size={13} className="text-gray-400 cursor-help" />
    </span>
  );
}

export default function TrainPage() {
  const { jobId: paramId } = useParams<{ jobId: string }>();
  const [resolvedJobId, setResolvedJobId] = useState<string | null>(null);
  const [taskType, setTaskType] = useState("segment");
  const [variant, setVariant] = useState("");
  const [epochs, setEpochs] = useState(100);
  const [batchSize, setBatchSize] = useState(8);
  const [imgsz, setImgsz] = useState(640);
  const [augPreset, setAugPreset] = useState("standard");
  const [patience, setPatience] = useState(2);
  const [resumeFrom, setResumeFrom] = useState<string>("");
  const [showNewRun, setShowNewRun] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [wsMetrics, setWsMetrics] = useState<Record<string, number>>({});
  const [lossHistory, setLossHistory] = useState<Record<string, number>[]>([]);
  const [metricHistory, setMetricHistory] = useState<Record<string, number>[]>([]);
  const [valPreview, setValPreview] = useState<string | null>(null);
  const [overfitWarning, setOverfitWarning] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<{ batch: number; total: number; losses: Record<string, number> } | null>(null);
  const [stopping, setStopping] = useState(false);
  const [previewExpanded, setPreviewExpanded] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // Try to load paramId as a training run first, then fall back to job ID
  const { data: directRun } = useQuery({
    queryKey: ["training-direct", paramId],
    queryFn: () => getTrainingStatus(paramId!),
    enabled: !!paramId && !runId,
    retry: false,
  });

  // If direct run lookup succeeds, use it; otherwise treat paramId as a job ID
  const jobId = directRun ? null : (resolvedJobId || paramId);

  useEffect(() => {
    if (directRun && !runId) {
      setRunId(directRun.run_id);
      if (directRun.job_id) setResolvedJobId(directRun.job_id);
    }
  }, [directRun, runId]);

  const { data: job } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJobStatus(jobId!),
    enabled: !!jobId,
  });

  const { data: variants } = useQuery({
    queryKey: ["variants"],
    queryFn: getVariants,
  });

  // Auto-detect existing training runs for this job (unless user wants a new run)
  const { data: allRuns } = useQuery({
    queryKey: ["training-runs"],
    queryFn: listTrainingRuns,
    enabled: !!jobId && !runId && !showNewRun,
  });

  // Fetch models for the "fine-tune from" dropdown
  const { data: existingModels } = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
  });

  useEffect(() => {
    if (jobId && !runId && !showNewRun && allRuns) {
      const existingRun = allRuns.find((r) => r.job_id === jobId);
      if (existingRun) {
        setRunId(existingRun.run_id);
      }
    }
  }, [jobId, runId, showNewRun, allRuns]);

  const { data: runStatus } = useQuery({
    queryKey: ["training", runId],
    queryFn: () => getTrainingStatus(runId!),
    enabled: !!runId,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "completed" || s === "failed" ? false : 3000;
    },
  });

  // Set default variant when task type changes
  useEffect(() => {
    if (variants?.defaults) {
      setVariant(variants.defaults[taskType] || "");
    }
  }, [taskType, variants]);

  // WebSocket for live metrics
  useEffect(() => {
    if (!runId || runStatus?.status === "completed" || runStatus?.status === "failed") return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/training/${runId}`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.metrics) setWsMetrics(data.metrics);
      if (data.loss_history) setLossHistory(data.loss_history);
      if (data.metric_history) setMetricHistory(data.metric_history);
      if (data.val_preview) { setValPreview(data.val_preview); }
      if (data.warning) setOverfitWarning(data.warning);
      // Batch-level progress
      if (data.batch != null && data.total_batches) {
        setBatchProgress({ batch: data.batch, total: data.total_batches, losses: data.batch_losses || {} });
      }
      // Clear batch progress on epoch end (when we get full metrics)
      if (data.loss_history) setBatchProgress(null);
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [runId, runStatus?.status]);

  // Esc to close lightbox
  useEffect(() => {
    if (!previewExpanded) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") setPreviewExpanded(false); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [previewExpanded]);

  const effectiveJobId = jobId || resolvedJobId || runStatus?.job_id;

  const handleTrain = useCallback(async () => {
    if (!effectiveJobId) return;
    setError("");
    try {
      const result = await startTraining(effectiveJobId, {
        task_type: taskType,
        model_variant: variant,
        hyperparameters: {
          epochs, batch: batchSize, imgsz, patience, augmentation: augPreset,
          ...(resumeFrom ? { resume_from: resumeFrom } : {}),
        },
      });
      setRunId(result.run_id);
      setShowNewRun(false);
    } catch (e: any) {
      setError(e.message);
    }
  }, [effectiveJobId, taskType, variant, epochs, batchSize, imgsz, augPreset, resumeFrom]);

  const metrics = runStatus?.metrics || wsMetrics;
  const progress = runStatus ? (runStatus.epoch_current / runStatus.total_epochs) * 100 : 0;

  // Use persisted history from API when WS history is empty (completed runs)
  const effectiveLossHistory = lossHistory.length > 0 ? lossHistory : (runStatus?.loss_history || []);
  const effectiveMetricHistory = metricHistory.length > 0 ? metricHistory : (runStatus?.metric_history || []);

  // Filter variants by task type
  const filteredVariants = variants
    ? Object.keys(variants.variants).filter((v) => {
        if (taskType === "segment") return v.includes("-seg");
        if (taskType === "detect") return !v.includes("-") || v === variant;
        if (taskType === "classify") return v.includes("-cls");
        if (taskType === "pose") return v.includes("-pose");
        if (taskType === "obb") return v.includes("-obb");
        return true;
      })
    : [];

  const frameCount = job?.total_frames || 0;

  return (
    <div className="min-h-screen">

      <div className="max-w-4xl mx-auto mt-8 px-4">
        <p className="eyebrow" style={{ marginBottom: 4 }}>Model training</p>
        <h1 className="text-2xl font-bold mb-2" style={{ color: "var(--text-primary)" }}>Train Model</h1>
        {job && (
          <p className="text-sm mb-6" style={{ color: "var(--text-secondary)" }}>
            Dataset: {job.name || job.text_prompt || "exemplar"} &middot; {job.total_frames} video{job.total_frames !== 1 ? "s" : ""}
            {job.annotation_count != null && <> &middot; {job.annotation_count} annotations</>}
            {job.class_count != null && job.class_count > 0 && <> &middot; {job.class_count} class{job.class_count !== 1 ? "es" : ""}</>}
          </p>
        )}

        {/* Dataset size warning */}
        {frameCount > 0 && frameCount < 50 && !runId && (
          <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg p-3 mb-5 text-sm text-amber-800">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>
              This dataset has only <strong>{frameCount}</strong> frames.
              For best results, aim for 100-200+ labeled frames per class.
              Training may still work but accuracy will be limited.
            </span>
          </div>
        )}

        {/* Config form — shown when no active run OR user wants a new run */}
        {(!runId || showNewRun) && (
          <div className="space-y-4 max-w-lg">
            {/* Presets */}
            <div>
              <label className="eyebrow block mb-1.5">Preset</label>
              <div className="flex gap-2">
                {PRESETS.map((p) => {
                  const active = epochs === p.epochs && batchSize === p.batch && imgsz === p.imgsz;
                  return (
                    <button
                      key={p.label}
                      onClick={() => {
                        setEpochs(p.epochs);
                        setBatchSize(p.batch);
                        setImgsz(p.imgsz);
                      }}
                      className="px-4 py-1.5 rounded-lg text-sm transition-colors"
                      style={active
                        ? { backgroundColor: "var(--text-primary)", color: "var(--bg-page)", border: "1px solid var(--text-primary)" }
                        : { backgroundColor: "var(--bg-surface)", color: "var(--text-secondary)", border: "1px solid var(--border-default)" }
                      }
                    >
                      {p.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div>
              <label className="eyebrow block mb-1">Task Type</label>
              <TaskSelector value={taskType} onChange={setTaskType} />
            </div>

            <div>
              <label className="eyebrow block mb-1">Model Variant</label>
              <select
                value={variant}
                onChange={(e) => setVariant(e.target.value)}
                className="rounded-lg px-3 py-2 text-sm w-full outline-none" style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
              >
                {filteredVariants.map((v) => (
                  <option key={v} value={v}>{humanizeVariant(v)}</option>
                ))}
              </select>
            </div>

            {/* Fine-tune from existing model */}
            <div>
              <label className="eyebrow block mb-1">
                Starting Weights
                <Tooltip text="Start from pretrained YOLO weights (default) or fine-tune from one of your existing models. Fine-tuning preserves learned features and trains faster." />
              </label>
              <select
                value={resumeFrom}
                onChange={(e) => setResumeFrom(e.target.value)}
                className="rounded-lg px-3 py-2 text-sm w-full outline-none" style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
              >
                <option value="">Pretrained (start fresh)</option>
                {existingModels?.filter((m) => m.task_type === taskType).map((m) => (
                  <option key={m.id} value={m.id}>
                    Fine-tune: {m.name} ({m.model_variant})
                  </option>
                ))}
              </select>
              {resumeFrom && (
                <p className="text-xs text-blue-600 mt-1">
                  Fine-tuning from existing checkpoint — training will start with learned features.
                </p>
              )}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <div>
                <label className="eyebrow block mb-1">
                  Epochs
                  <Tooltip text="Max training passes. Training may stop earlier if no improvement is seen (see Patience)." />
                </label>
                <input
                  type="number"
                  value={epochs}
                  onChange={(e) => setEpochs(Number(e.target.value))}
                  className="rounded-lg px-3 py-2 text-sm w-full outline-none" style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                />
              </div>
              <div>
                <label className="eyebrow block mb-1">
                  Patience
                  <Tooltip text="Stop training after this many epochs without improvement on validation metrics. Default 2 means training stops if val loss increases for 2 consecutive epochs." />
                </label>
                <input
                  type="number"
                  value={patience}
                  onChange={(e) => setPatience(Number(e.target.value))}
                  min={1}
                  className="rounded-lg px-3 py-2 text-sm w-full outline-none" style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                />
              </div>
              <div>
                <label className="eyebrow block mb-1">
                  Batch Size
                  <Tooltip text="Images per batch. Larger = faster training but more memory. Reduce if you get out-of-memory errors." />
                </label>
                <input
                  type="number"
                  value={batchSize}
                  onChange={(e) => setBatchSize(Number(e.target.value))}
                  className="rounded-lg px-3 py-2 text-sm w-full outline-none" style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                />
              </div>
              <div>
                <label className="eyebrow block mb-1">
                  Image Size
                  <Tooltip text="Input resolution. Larger detects smaller objects but trains slower. Common: 640, 1280." />
                </label>
                <input
                  type="number"
                  value={imgsz}
                  onChange={(e) => setImgsz(Number(e.target.value))}
                  className="rounded-lg px-3 py-2 text-sm w-full outline-none" style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                />
              </div>
            </div>

            {/* Augmentation preset */}
            <div>
              <label className="eyebrow block mb-1.5">
                Data Augmentation
                <Tooltip text="Controls how training images are transformed. Stronger augmentation makes models more robust to lighting, angles, and occlusion — but trains slower." />
              </label>
              <div className="space-y-2">
                {([
                  { value: "minimal", label: "Minimal", desc: "No mosaic or mixup. Fastest training, least robust." },
                  { value: "standard", label: "Standard (Recommended)", desc: "Mosaic + mixup + rotation + copy-paste. Good balance of speed and robustness." },
                  { value: "aggressive", label: "Aggressive", desc: "Heavy augmentation: strong rotation, perspective warp, multi-scale. Best for out-of-distribution robustness." },
                ] as const).map((opt) => (
                  <label
                    key={opt.value}
                    className="flex items-start gap-3 p-3 rounded-lg cursor-pointer transition-colors"
                    style={{
                      border: augPreset === opt.value ? "2px solid var(--accent)" : "2px solid var(--border-subtle)",
                      backgroundColor: augPreset === opt.value ? "var(--accent-soft)" : "transparent",
                    }}
                  >
                    <input
                      type="radio"
                      name="augmentation"
                      value={opt.value}
                      checked={augPreset === opt.value}
                      onChange={() => setAugPreset(opt.value)}
                      className="mt-0.5"
                    />
                    <div>
                      <span className="text-sm font-medium">{opt.label}</span>
                      <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{opt.desc}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            <button
              onClick={handleTrain}
              className="px-6 py-2 text-white rounded-lg"
              style={{ backgroundColor: "var(--accent)" }}
            >
              Start Training
            </button>
            {error && <p className="text-red-600 text-sm">{error}</p>}
          </div>
        )}

        {/* Training progress */}
        {runStatus && (
          <div className="mt-6 space-y-4">
            {/* Progress bar + stop button */}
            <div className="surface p-5" style={{ borderRadius: "var(--radius-lg)" }}>
              <div className="flex justify-between items-center mb-2">
                <div>
                  <span className="font-medium capitalize" style={{ color: "var(--text-primary)" }}>{runStatus.status}</span>
                  {runStatus.status === "training" && batchProgress && batchProgress.total > 0 && (
                    <span style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginLeft: 8 }}>
                      Batch {batchProgress.batch}/{batchProgress.total} ({Math.round((batchProgress.batch / batchProgress.total) * 100)}%)
                    </span>
                  )}
                  {runStatus.status === "preparing" && (
                    <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: 8 }}>Preparing dataset...</span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <span style={{ fontSize: 13, color: "var(--text-secondary)", fontFamily: "var(--font-mono)" }}>
                    Epoch {runStatus.epoch_current}/{runStatus.total_epochs}
                    {/* ETA estimate from epoch timing */}
                    {runStatus.status === "training" && runStatus.epoch_current > 1 && runStatus.started_at && (() => {
                      const elapsed = (Date.now() - new Date(runStatus.started_at).getTime()) / 1000;
                      const secPerEpoch = elapsed / runStatus.epoch_current;
                      const remaining = (runStatus.total_epochs - runStatus.epoch_current) * secPerEpoch;
                      const mins = Math.round(remaining / 60);
                      return (
                        <span style={{ color: "var(--text-muted)", marginLeft: 8, fontSize: 11 }}>
                          ~{mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${mins % 60}m`} remaining
                        </span>
                      );
                    })()}
                  </span>
                  {runStatus.status === "training" && (
                    <button
                      onClick={async () => {
                        setStopping(true);
                        try { await stopTraining(runId!); } catch (e) { console.error(e); }
                      }}
                      disabled={stopping}
                      className="flex items-center gap-1 px-3 py-1 rounded-lg text-xs disabled:opacity-50"
                      style={{ backgroundColor: "var(--danger-soft)", color: "var(--danger)", border: "1px solid var(--danger)" }}
                    >
                      <StopCircle size={12} />
                      {stopping ? "Stopping..." : "Stop Early"}
                    </button>
                  )}
                </div>
              </div>
              {/* Epoch progress */}
              <div className="w-full rounded-full h-2 mb-1" style={{ backgroundColor: "var(--bg-inset)" }}>
                <div
                  className="h-2 rounded-full transition-all"
                  style={{ width: `${progress}%`, backgroundColor: "var(--accent)" }}
                />
              </div>
              {/* Batch sub-progress within current epoch */}
              {runStatus.status === "training" && batchProgress && batchProgress.total > 0 && (
                <div className="mt-2">
                  <div className="w-full rounded-full h-1.5" style={{ backgroundColor: "var(--bg-inset)" }}>
                    <div
                      className="h-1.5 rounded-full transition-all"
                      style={{ width: `${(batchProgress.batch / batchProgress.total) * 100}%`, backgroundColor: "var(--accent)" }}
                    />
                  </div>
                  {Object.keys(batchProgress.losses).length > 0 && (
                    <div className="flex gap-4 mt-2" style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {Object.entries(batchProgress.losses).map(([k, v]) => (
                        <span key={k}>
                          {k.replace("train/", "")}: <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{v.toFixed(3)}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Run configuration — collapsible summary of hyperparameters used */}
            {Object.keys(runStatus.hyperparameters).length > 0 && (
              <div className="surface p-4" style={{ borderRadius: "var(--radius-lg)" }}>
                <p className="eyebrow mb-2">Configuration</p>
                <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
                  {[
                    { label: "Model", value: runStatus.model_variant },
                    { label: "Epochs", value: runStatus.total_epochs },
                    { label: "Batch", value: runStatus.hyperparameters.batch ?? "-" },
                    { label: "Img Size", value: runStatus.hyperparameters.imgsz ?? "-" },
                    { label: "Patience", value: runStatus.hyperparameters.patience ?? "-" },
                    { label: "Augment", value: runStatus.hyperparameters.augmentation ?? "-" },
                  ].map((p) => (
                    <div key={p.label}>
                      <span className="block" style={{ fontSize: 10, fontFamily: "var(--font-mono)", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>{p.label}</span>
                      <span style={{ fontSize: 14, fontWeight: 600, fontFamily: "var(--font-serif)", color: "var(--text-primary)" }}>
                        {String(p.value)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Overfitting warning */}
            {overfitWarning && (
              <div className="flex items-start gap-2 rounded-lg p-3 text-sm" style={{ backgroundColor: "var(--warning-soft)", border: "1px solid var(--warning)", color: "var(--warning)" }}>
                <TrendingDown size={16} className="mt-0.5 shrink-0" />
                <div>
                  <span className="font-medium">Overfitting detected: </span>
                  {overfitWarning}
                  <p className="text-xs mt-1" style={{ opacity: 0.8 }}>
                    Consider stopping early or adding more training data.
                  </p>
                </div>
              </div>
            )}

            {/* No history message for old runs */}
            {effectiveLossHistory.length === 0 && runStatus.status === "completed" && (
              <div className="surface p-5" style={{ borderRadius: "var(--radius-lg)" }}>
                <p className="eyebrow mb-2">Training Curves</p>
                <p className="text-sm" style={{ color: "var(--text-muted)" }}>
                  Epoch-by-epoch curves are not available for this run (trained before curve tracking was added).
                </p>
              </div>
            )}

            {/* Loss curves */}
            {effectiveLossHistory.length > 1 && (() => {
              // Find best epoch (lowest val box loss)
              let bestLossEpoch = -1;
              let bestLossVal = Infinity;
              effectiveLossHistory.forEach((row, i) => {
                for (const [k, v] of Object.entries(row)) {
                  if (k.includes("val") && k.includes("box_loss") && v < bestLossVal) {
                    bestLossVal = v;
                    bestLossEpoch = i;
                  }
                }
              });
              const allKeys = Object.keys(effectiveLossHistory[0] || {}).filter((k) => k !== "epoch");
              const lossSeries = allKeys.map((k) => {
                const isVal = k.startsWith("val/");
                const shortName = k.replace("val/", "").replace("train/", "");
                return {
                  key: k,
                  label: `${isVal ? "Val" : "Train"} ${shortName}`,
                  color: isVal ? "#ef4444" : "#3b82f6",
                };
              });
              // Group: show box+seg+cls losses with train vs val overlay
              const boxKeys = lossSeries.filter((s) => s.key.includes("box_loss"));
              const segKeys = lossSeries.filter((s) => s.key.includes("seg_loss"));
              const clsKeys = lossSeries.filter((s) => s.key.includes("cls_loss"));
              const otherKeys = lossSeries.filter((s) =>
                !s.key.includes("box_loss") && !s.key.includes("seg_loss") && !s.key.includes("cls_loss")
              );
              const groups = [
                { title: "Box Loss", series: boxKeys },
                { title: "Seg Loss", series: segKeys },
                { title: "Cls Loss", series: clsKeys },
                ...(otherKeys.length > 0 ? [{ title: "Other Loss", series: otherKeys }] : []),
              ].filter((g) => g.series.length > 0);

              return (
                <div className="surface p-5" style={{ borderRadius: "var(--radius-lg)" }}>
                  <p className="eyebrow mb-3">Loss Curves</p>
                  <div className={`grid gap-4 ${groups.length > 1 ? "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3" : ""}`}>
                    {groups.map((g) => (
                      <div key={g.title}>
                        <p className="eyebrow mb-1">{g.title}</p>
                        <LineChart data={effectiveLossHistory} series={g.series} height={120} bestEpoch={bestLossEpoch >= 0 ? bestLossEpoch : undefined} />
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Accuracy metrics */}
            {effectiveMetricHistory.length > 1 && (() => {
              // Find best epoch (highest mAP50)
              let bestMetricEpoch = -1;
              let bestMetricVal = -1;
              effectiveMetricHistory.forEach((row, i) => {
                for (const [k, v] of Object.entries(row)) {
                  if (k.includes("mAP50") && k.includes("(B)") && v > bestMetricVal) {
                    bestMetricVal = v;
                    bestMetricEpoch = i;
                  }
                }
              });
              const keys = Object.keys(effectiveMetricHistory[0] || {}).filter((k) => k !== "epoch");
              const METRIC_COLORS: Record<string, string> = {
                "precision": "#8b5cf6",
                "recall": "#f59e0b",
                "mAP50": "#22c55e",
                "mAP50-95": "#14b8a6",
              };
              const series = keys.map((k) => {
                const short = k.replace("metrics/", "").replace("(B)", "").replace("(M)", " mask");
                const colorKey = Object.keys(METRIC_COLORS).find((ck) => k.includes(ck));
                return {
                  key: k,
                  label: short,
                  color: colorKey ? METRIC_COLORS[colorKey] : "#6b7280",
                };
              });
              // Split bbox vs mask metrics
              const bboxSeries = series.filter((s) => s.key.includes("(B)"));
              const maskSeries = series.filter((s) => s.key.includes("(M)"));
              const groups = [
                ...(bboxSeries.length > 0 ? [{ title: "Detection Metrics", series: bboxSeries }] : []),
                ...(maskSeries.length > 0 ? [{ title: "Segmentation Metrics", series: maskSeries }] : []),
                ...(bboxSeries.length === 0 && maskSeries.length === 0 ? [{ title: "Metrics", series }] : []),
              ];

              return (
                <div className="surface p-5" style={{ borderRadius: "var(--radius-lg)" }}>
                  <p className="eyebrow mb-3">Accuracy Metrics</p>
                  <div className={`grid gap-4 ${groups.length > 1 ? "grid-cols-1 sm:grid-cols-2" : ""}`}>
                    {groups.map((g) => (
                      <div key={g.title}>
                        <p className="eyebrow mb-1">{g.title}</p>
                        <LineChart data={effectiveMetricHistory} series={g.series} height={140} yMin={0} yMax={1} bestEpoch={bestMetricEpoch >= 0 ? bestMetricEpoch : undefined} />
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Live metrics grid */}
            {Object.keys(metrics).length > 0 && (
              <div className="surface p-4" style={{ borderRadius: "var(--radius-lg)" }}>
                <p className="eyebrow mb-3">Current Metrics</p>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {Object.entries(metrics)
                    .filter(([k]) => !k.includes("sem_loss") && k !== "fitness")
                    .map(([k, v]) => (
                    <div key={k} className="rounded-lg p-2.5" style={{ backgroundColor: "var(--bg-inset)" }}>
                      <span className="eyebrow block">{humanizeMetricKey(k)}</span>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 14, fontWeight: 600, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
                        {typeof v === "number" ? formatMetricValue(k, v) : String(v)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Per-class metrics (from best_metrics — YOLO computes these) */}
            {runStatus.best_metrics && (() => {
              const classMetrics: { name: string; precision: number; recall: number; mAP50: number }[] = [];
              // YOLO stores per-class as metrics/precision_class0, etc. — or in the raw metrics dict
              // Check for per-class keys pattern
              for (const [k, v] of Object.entries(runStatus.best_metrics)) {
                if (k.startsWith("per_class/")) {
                  const parts = k.replace("per_class/", "").split("/");
                  if (parts.length === 2) {
                    const [cls, metric] = parts;
                    let entry = classMetrics.find((c) => c.name === cls);
                    if (!entry) {
                      entry = { name: cls, precision: 0, recall: 0, mAP50: 0 };
                      classMetrics.push(entry);
                    }
                    if (metric === "precision") entry.precision = v;
                    if (metric === "recall") entry.recall = v;
                    if (metric === "mAP50") entry.mAP50 = v;
                  }
                }
              }
              if (classMetrics.length === 0) return null;
              return (
                <div className="surface p-4" style={{ borderRadius: "var(--radius-lg)" }}>
                  <p className="eyebrow mb-3">Per-Class Performance</p>
                  <div className="space-y-1.5">
                    <div className="grid grid-cols-4 gap-2 text-xs" style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      <span>Class</span><span className="text-right">Precision</span><span className="text-right">Recall</span><span className="text-right">mAP50</span>
                    </div>
                    {classMetrics.map((c) => (
                      <div key={c.name} className="grid grid-cols-4 gap-2 text-sm" style={{ fontVariantNumeric: "tabular-nums" }}>
                        <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{c.name}</span>
                        <span className="text-right" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{(c.precision * 100).toFixed(1)}%</span>
                        <span className="text-right" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{(c.recall * 100).toFixed(1)}%</span>
                        <span className="text-right" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{(c.mAP50 * 100).toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Validation/training preview image — always shown */}
            {valPreview && (
              <div className="surface p-5" style={{ borderRadius: "var(--radius-lg)" }}>
                <p className="eyebrow mb-3 flex items-center gap-2">
                  <Eye size={12} />
                  Validation Preview
                </p>
                <img
                  src={`data:image/jpeg;base64,${valPreview}`}
                  alt="Model predictions"
                  className="rounded-lg w-full cursor-pointer hover:opacity-90 transition-opacity"
                  style={{ border: "1px solid var(--border-default)" }}
                  onClick={() => setPreviewExpanded(true)}
                />
                <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
                  Updated each epoch. Click to enlarge.
                </p>
              </div>
            )}

            {/* Expanded preview lightbox */}
            {previewExpanded && valPreview && (
              <div
                className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-8 cursor-pointer"
                onClick={() => setPreviewExpanded(false)}
              >
                <div className="relative max-w-full max-h-full" onClick={(e) => e.stopPropagation()}>
                  <img
                    src={`data:image/jpeg;base64,${valPreview}`}
                    alt="Model predictions (enlarged)"
                    className="max-w-full max-h-[90vh] object-contain rounded-lg"
                  />
                  <button
                    onClick={() => setPreviewExpanded(false)}
                    className="absolute top-3 right-3 w-8 h-8 bg-gray-900/80 hover:bg-gray-800 rounded-full flex items-center justify-center text-white text-sm"
                  >
                    &times;
                  </button>
                  <p className="text-center text-gray-400 text-xs mt-3">
                    Click outside or press Esc to close
                  </p>
                </div>
              </div>
            )}

            {/* Completed — Rich results summary */}
            {runStatus.status === "completed" && (() => {
              const bm = runStatus.best_metrics || {};
              const mAP50 = bm["metrics/mAP50(B)"];
              const mAP5095 = bm["metrics/mAP50-95(B)"];
              const prec = bm["metrics/precision(B)"];
              const rec = bm["metrics/recall(B)"];
              const duration = runStatus.started_at && runStatus.completed_at
                ? (() => {
                    const secs = Math.round((new Date(runStatus.completed_at).getTime() - new Date(runStatus.started_at).getTime()) / 1000);
                    return secs < 60 ? `${secs}s` : secs < 3600 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
                  })()
                : null;

              return (
                <div className="surface p-5" style={{ borderRadius: "var(--radius-lg)", borderColor: "var(--success)" }}>
                  <div className="flex items-center gap-2 mb-4">
                    <CheckCircle size={18} style={{ color: "var(--success)" }} />
                    <span style={{ fontFamily: "var(--font-serif)", fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
                      Training Complete
                    </span>
                    <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginLeft: "auto" }}>
                      {runStatus.epoch_current} epochs{duration && <> · {duration}</>}
                    </span>
                  </div>

                  {/* Hero metrics */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                    {[
                      { label: "mAP50", value: mAP50, hero: true },
                      { label: "mAP50-95", value: mAP5095 },
                      { label: "Precision", value: prec },
                      { label: "Recall", value: rec },
                    ].map((m) => (
                      <div
                        key={m.label}
                        className="rounded-lg p-3 text-center"
                        style={{ backgroundColor: m.hero ? "var(--success-soft)" : "var(--bg-inset)" }}
                      >
                        <span className="eyebrow block">{m.label}</span>
                        <span style={{
                          fontSize: m.hero ? 28 : 22,
                          fontWeight: 700,
                          fontFamily: "var(--font-serif)",
                          color: m.hero ? "var(--success)" : "var(--text-primary)",
                          fontVariantNumeric: "tabular-nums",
                        }}>
                          {m.value != null ? `${(m.value * 100).toFixed(1)}%` : "—"}
                        </span>
                      </div>
                    ))}
                  </div>

                  {/* Actions */}
                  <div className="flex flex-wrap gap-3">
                    <button
                      onClick={async () => {
                        const allModels = await listModels();
                        const thisModel = allModels.find((m) => m.name?.includes(runStatus.name || ""));
                        if (thisModel) {
                          await activateModel(thisModel.id);
                          window.location.href = "/deploy/test";
                        } else {
                          window.location.href = "/deploy/models";
                        }
                      }}
                      className="px-4 py-2 text-white rounded-lg text-sm font-medium"
                      style={{ backgroundColor: "var(--success)" }}
                    >
                      Activate &amp; Try Demo
                    </button>
                    <button
                      onClick={() => { setShowNewRun(true); setRunId(null); }}
                      className="flex items-center gap-1.5 px-4 py-2 text-white rounded-lg text-sm font-medium"
                      style={{ backgroundColor: "var(--accent)" }}
                    >
                      <RotateCcw size={14} />
                      Train Again
                    </button>
                    <Link
                      to="/experiments"
                      className="px-4 py-2 rounded-lg text-sm"
                      style={{ border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}
                    >
                      View Experiments
                    </Link>
                    {runStatus.weights_url && (
                      <a
                        href={runStatus.weights_url}
                        className="px-4 py-2 rounded-lg text-sm"
                        style={{ border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}
                      >
                      Download Weights
                    </a>
                  )}
                </div>
              </div>
              );
            })()}

            {runStatus.status === "failed" && (
              <div className="rounded-lg p-4" style={{ backgroundColor: "var(--danger-soft)", border: "1px solid var(--danger)" }}>
                <h3 className="font-semibold mb-1" style={{ color: "var(--danger)" }}>Training Failed</h3>
                <p className="text-sm" style={{ color: "var(--danger)" }}>{runStatus.error_message}</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
