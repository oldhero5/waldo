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
import { Info, AlertTriangle, StopCircle, TrendingDown, Eye, RotateCcw } from "lucide-react";
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
        <h1 className="text-2xl font-bold text-gray-900 mb-2">Train Model</h1>
        {job && (
          <p className="text-gray-500 text-sm mb-6">
            Dataset: {job.text_prompt || "exemplar"} &middot; {job.total_frames} frames
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
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Preset</label>
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
                      className={`px-4 py-1.5 rounded-lg text-sm border transition-colors ${
                        active
                          ? "bg-gray-900 text-white border-gray-900"
                          : "bg-white text-gray-600 border-gray-200 hover:bg-gray-50"
                      }`}
                    >
                      {p.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Task Type</label>
              <TaskSelector value={taskType} onChange={setTaskType} />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Model Variant</label>
              <select
                value={variant}
                onChange={(e) => setVariant(e.target.value)}
                className="border rounded px-3 py-2 text-sm bg-white w-full"
              >
                {filteredVariants.map((v) => (
                  <option key={v} value={v}>{humanizeVariant(v)}</option>
                ))}
              </select>
            </div>

            {/* Fine-tune from existing model */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Starting Weights
                <Tooltip text="Start from pretrained YOLO weights (default) or fine-tune from one of your existing models. Fine-tuning preserves learned features and trains faster." />
              </label>
              <select
                value={resumeFrom}
                onChange={(e) => setResumeFrom(e.target.value)}
                className="border rounded px-3 py-2 text-sm bg-white w-full"
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
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Epochs
                  <Tooltip text="Max training passes. Training may stop earlier if no improvement is seen (see Patience)." />
                </label>
                <input
                  type="number"
                  value={epochs}
                  onChange={(e) => setEpochs(Number(e.target.value))}
                  className="border rounded px-3 py-2 text-sm w-full"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Patience
                  <Tooltip text="Stop training after this many epochs without improvement on validation metrics. Default 2 means training stops if val loss increases for 2 consecutive epochs." />
                </label>
                <input
                  type="number"
                  value={patience}
                  onChange={(e) => setPatience(Number(e.target.value))}
                  min={1}
                  className="border rounded px-3 py-2 text-sm w-full"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Batch Size
                  <Tooltip text="Images per batch. Larger = faster training but more memory. Reduce if you get out-of-memory errors." />
                </label>
                <input
                  type="number"
                  value={batchSize}
                  onChange={(e) => setBatchSize(Number(e.target.value))}
                  className="border rounded px-3 py-2 text-sm w-full"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Image Size
                  <Tooltip text="Input resolution. Larger detects smaller objects but trains slower. Common: 640, 1280." />
                </label>
                <input
                  type="number"
                  value={imgsz}
                  onChange={(e) => setImgsz(Number(e.target.value))}
                  className="border rounded px-3 py-2 text-sm w-full"
                />
              </div>
            </div>

            {/* Augmentation preset */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
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
                    className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                      augPreset === opt.value
                        ? "border-blue-500 bg-blue-50"
                        : "border-gray-200 hover:bg-gray-50"
                    }`}
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
                      <p className="text-xs text-gray-500 mt-0.5">{opt.desc}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            <button
              onClick={handleTrain}
              className="px-6 py-2 bg-gray-900 text-white rounded-lg"
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
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex justify-between items-center mb-2">
                <div>
                  <span className="font-medium capitalize">{runStatus.status}</span>
                  {runStatus.status === "training" && batchProgress && batchProgress.total > 0 && (
                    <span className="text-xs text-gray-400 ml-2">
                      Batch {batchProgress.batch}/{batchProgress.total} ({Math.round((batchProgress.batch / batchProgress.total) * 100)}%)
                    </span>
                  )}
                  {runStatus.status === "preparing" && (
                    <span className="text-xs text-gray-400 ml-2">Preparing dataset...</span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm text-gray-500">
                    Epoch {runStatus.epoch_current}/{runStatus.total_epochs}
                  </span>
                  {runStatus.status === "training" && (
                    <button
                      onClick={async () => {
                        setStopping(true);
                        try { await stopTraining(runId!); } catch {}
                      }}
                      disabled={stopping}
                      className="flex items-center gap-1 px-3 py-1 bg-red-50 text-red-600 border border-red-200 rounded-lg text-xs hover:bg-red-100 disabled:opacity-50"
                    >
                      <StopCircle size={12} />
                      {stopping ? "Stopping..." : "Stop Early"}
                    </button>
                  )}
                </div>
              </div>
              {/* Epoch progress */}
              <div className="w-full bg-gray-200 rounded-full h-2 mb-1">
                <div
                  className="bg-gray-900 h-2 rounded-full transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              {/* Batch sub-progress within current epoch */}
              {runStatus.status === "training" && batchProgress && batchProgress.total > 0 && (
                <div className="mt-2">
                  <div className="w-full bg-gray-100 rounded-full h-1.5">
                    <div
                      className="bg-blue-500 h-1.5 rounded-full transition-all"
                      style={{ width: `${(batchProgress.batch / batchProgress.total) * 100}%` }}
                    />
                  </div>
                  {/* Live batch losses */}
                  {Object.keys(batchProgress.losses).length > 0 && (
                    <div className="flex gap-4 mt-2 text-xs text-gray-400">
                      {Object.entries(batchProgress.losses).map(([k, v]) => (
                        <span key={k}>
                          {k.replace("train/", "")}: <span className="font-mono text-gray-600">{v.toFixed(3)}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Overfitting warning */}
            {overfitWarning && (
              <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
                <TrendingDown size={16} className="mt-0.5 shrink-0" />
                <div>
                  <span className="font-medium">Overfitting detected: </span>
                  {overfitWarning}
                  <p className="text-xs mt-1 text-amber-600">
                    Consider stopping early or adding more training data.
                  </p>
                </div>
              </div>
            )}

            {/* No history message for old runs */}
            {effectiveLossHistory.length === 0 && runStatus.status === "completed" && (
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="font-semibold text-gray-900 mb-2">Training Curves</h3>
                <p className="text-sm text-gray-500">
                  Epoch-by-epoch curves are not available for this run (trained before curve tracking was added).
                  New training runs will show full loss and accuracy curves here.
                </p>
              </div>
            )}

            {/* Loss curves */}
            {effectiveLossHistory.length > 1 && (() => {
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
                <div className="bg-white rounded-xl border border-gray-200 p-5">
                  <h3 className="font-semibold text-gray-900 mb-3">Loss Curves</h3>
                  <div className={`grid gap-4 ${groups.length > 1 ? "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3" : ""}`}>
                    {groups.map((g) => (
                      <div key={g.title}>
                        <p className="text-xs text-gray-500 mb-1 font-medium">{g.title}</p>
                        <LineChart data={lossHistory} series={g.series} height={120} />
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Accuracy metrics */}
            {effectiveMetricHistory.length > 1 && (() => {
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
                <div className="bg-white rounded-xl border border-gray-200 p-5">
                  <h3 className="font-semibold text-gray-900 mb-3">Accuracy Metrics</h3>
                  <div className={`grid gap-4 ${groups.length > 1 ? "grid-cols-1 sm:grid-cols-2" : ""}`}>
                    {groups.map((g) => (
                      <div key={g.title}>
                        <p className="text-xs text-gray-500 mb-1 font-medium">{g.title}</p>
                        <LineChart data={metricHistory} series={g.series} height={140} yMin={0} yMax={1} />
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Live metrics grid */}
            {Object.keys(metrics).length > 0 && (
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h3 className="font-semibold text-gray-900 mb-3">Current Metrics</h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {Object.entries(metrics)
                    .filter(([k]) => !k.includes("sem_loss") && k !== "fitness")
                    .map(([k, v]) => (
                    <div key={k} className="bg-gray-50 rounded-lg p-2">
                      <span className="text-xs text-gray-500 block">{humanizeMetricKey(k)}</span>
                      <span className="font-mono text-sm font-medium">
                        {typeof v === "number" ? formatMetricValue(k, v) : String(v)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Validation/training preview image — always shown */}
            {valPreview && (
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="font-semibold text-gray-900 flex items-center gap-2 mb-3">
                  <Eye size={16} />
                  Model Predictions on Test Data
                </h3>
                <img
                  src={`data:image/jpeg;base64,${valPreview}`}
                  alt="Model predictions"
                  className="rounded-lg border border-gray-200 w-full cursor-pointer hover:opacity-90 transition-opacity"
                  onClick={() => setPreviewExpanded(true)}
                />
                <p className="text-xs text-gray-400 mt-2">
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

            {/* Completed */}
            {runStatus.status === "completed" && (
              <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                <h3 className="font-semibold text-green-800 mb-2">Training Complete</h3>
                <p className="text-sm text-green-700 mb-3">
                  Finished at epoch {runStatus.epoch_current}/{runStatus.total_epochs}.
                  {runStatus.best_metrics?.["metrics/mAP50(B)"] != null && (
                    <> mAP50: {(runStatus.best_metrics["metrics/mAP50(B)"] * 100).toFixed(1)}%</>
                  )}
                </p>
                <div className="flex flex-wrap gap-3">
                  <button
                    onClick={async () => {
                      // Find the model from this run and activate + navigate to demo
                      const allModels = await listModels();
                      const thisModel = allModels.find((m) => m.name?.includes(runStatus.name || ""));
                      if (thisModel) {
                        await activateModel(thisModel.id);
                        window.location.href = "/demo";
                      } else {
                        window.location.href = "/deploy";
                      }
                    }}
                    className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium"
                  >
                    Activate &amp; Try Demo
                  </button>
                  <button
                    onClick={() => { setShowNewRun(true); setRunId(null); }}
                    className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
                  >
                    <RotateCcw size={14} />
                    Train Again
                  </button>
                  <Link
                    to="/experiments"
                    className="px-4 py-2 border border-green-300 text-green-700 rounded-lg text-sm hover:bg-green-100"
                  >
                    View Experiments
                  </Link>
                  {runStatus.weights_url && (
                    <a
                      href={runStatus.weights_url}
                      className="px-4 py-2 border border-green-300 text-green-700 rounded-lg text-sm hover:bg-green-100"
                    >
                      Download Weights
                    </a>
                  )}
                </div>
              </div>
            )}

            {runStatus.status === "failed" && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                <h3 className="font-semibold text-red-800 mb-1">Training Failed</h3>
                <p className="text-sm text-red-600">{runStatus.error_message}</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
