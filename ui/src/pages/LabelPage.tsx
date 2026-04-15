import { useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  getJobStatus,
  listFrames,
  previewPrompts,
  startExemplarLabeling,
  startLabeling,
  type ClassPrompt,
  type FrameOut,
  type PreviewResponse,
} from "../api";
import ClickCanvas from "../components/ClickCanvas";
import TaskSelector from "../components/TaskSelector";

type Mode = "text" | "exemplar";

interface ClickPoint {
  x: number;
  y: number;
  label: number;
}

interface ClassEntry {
  name: string;
  prompt: string;
}

export default function LabelPage() {
  const { videoId, projectId } = useParams<{ videoId?: string; projectId?: string }>();
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("text");
  const [classEntries, setClassEntries] = useState<ClassEntry[]>([{ name: "", prompt: "" }]);
  const [taskType, setTaskType] = useState("segment");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [points, setPoints] = useState<ClickPoint[]>([]);
  const [selectedFrame, setSelectedFrame] = useState<FrameOut | null>(null);
  const [className, setClassName] = useState("object");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewThreshold, setPreviewThreshold] = useState(0.35);
  const [previewFrames, setPreviewFrames] = useState(8);

  const { data: frames } = useQuery({
    queryKey: ["frames", videoId],
    queryFn: () => listFrames(videoId!),
    enabled: !!videoId && !projectId,
  });

  const { data: jobStatus } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJobStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "completed" || status === "failed" ? false : 2000;
    },
  });

  const updateClassEntry = (index: number, field: keyof ClassEntry, value: string) => {
    setClassEntries((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      if (field === "prompt" && !next[index].name) {
        next[index].name = value;
      }
      return next;
    });
  };

  const addClassEntry = () => {
    setClassEntries((prev) => [...prev, { name: "", prompt: "" }]);
  };

  const removeClassEntry = (index: number) => {
    setClassEntries((prev) => prev.filter((_, i) => i !== index));
  };

  const validEntries = classEntries.filter((e) => e.prompt.trim());

  const handleTextLabel = useCallback(async () => {
    if (validEntries.length === 0) return;
    if (!videoId && !projectId) return;
    setError("");
    try {
      const classPrompts: ClassPrompt[] = validEntries.map((e) => {
        const aliases = e.prompt.split(",").map((s) => s.trim()).filter(Boolean);
        return {
          name: e.name.trim() || aliases[0],
          ...(aliases.length > 1 ? { prompts: aliases } : { prompt: aliases[0] }),
        };
      });

      const result = await startLabeling({
        videoId: videoId || undefined,
        projectId: projectId || undefined,
        classPrompts,
        taskType,
      });
      setJobId(result.job_id);
    } catch (e: any) {
      setError(e.message);
    }
  }, [videoId, projectId, validEntries, taskType]);

  const handlePreview = useCallback(async () => {
    if (validEntries.length === 0 || (!videoId && !projectId)) return;
    setPreviewing(true);
    setPreview(null);
    setError("");
    try {
      const allPrompts = validEntries.flatMap((e) =>
        e.prompt.split(",").map((s) => s.trim()).filter(Boolean)
      );
      const result = await previewPrompts({
        videoId: videoId || undefined,
        projectId: projectId || undefined,
        prompts: allPrompts,
        maxFrames: previewFrames,
        threshold: previewThreshold,
      });
      setPreview(result);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setPreviewing(false);
    }
  }, [videoId, projectId, validEntries, previewThreshold, previewFrames]);

  const handleExemplarLabel = useCallback(async () => {
    if (!videoId || !selectedFrame || points.length === 0) return;
    setError("");
    try {
      const result = await startExemplarLabeling(
        videoId,
        selectedFrame.frame_number,
        points.map((p) => [p.x, p.y]),
        points.map((p) => p.label),
        taskType,
        className
      );
      setJobId(result.job_id);
    } catch (e: any) {
      setError(e.message);
    }
  }, [videoId, selectedFrame, points, taskType, className]);

  const isRunning = jobStatus && !["completed", "failed"].includes(jobStatus.status);
  const title = projectId ? "Label Collection" : "Label Video";

  return (
    <div className="min-h-screen">

      <div className="max-w-3xl mx-auto mt-6 px-4 pb-12">
        {/* Header */}
        <div className="surface p-6 mb-6" style={{ borderRadius: "var(--radius-lg)" }}>
          <p className="eyebrow" style={{ marginBottom: 4 }}>Auto-labeling</p>
          <h1 style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 700, color: "var(--text-primary)" }}>{title}</h1>

          {frames && frames.length > 0 && !projectId && (
            <div className="flex items-center gap-3 mt-3 p-3 rounded-lg" style={{ backgroundColor: "var(--bg-inset)" }}>
              <img
                src={frames[0].image_url}
                alt="Video preview"
                className="w-24 h-16 object-cover rounded"
                style={{ border: "1px solid var(--border-default)" }}
              />
              <div>
                <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{frames.length} frames extracted</p>
                {frames[0].width && frames[0].height && (
                  <p style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                    {frames[0].width} &times; {frames[0].height}px
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Method selection */}
        <div className="surface p-6 mb-6" style={{ borderRadius: "var(--radius-lg)" }}>
          <p className="eyebrow mb-3">Labeling method</p>
          {!projectId && (
            <div className="grid grid-cols-2 gap-3 mb-4">
              {[
                { key: "text" as Mode, label: "Describe with text", desc: "Type what you're looking for and the AI will find it in every frame." },
                { key: "exemplar" as Mode, label: "Click on examples", desc: "Point at objects in a frame and the AI will track them across the video." },
              ].map((opt) => (
                <button
                  key={opt.key}
                  className="p-4 rounded-lg text-left transition-all"
                  style={{
                    border: mode === opt.key ? "2px solid var(--accent)" : "2px solid var(--border-subtle)",
                    backgroundColor: mode === opt.key ? "var(--accent-soft)" : "transparent",
                  }}
                  onClick={() => setMode(opt.key)}
                >
                  <p className="font-medium text-sm" style={{ color: "var(--text-primary)" }}>{opt.label}</p>
                  <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>{opt.desc}</p>
                </button>
              ))}
            </div>
          )}

          <div className="flex items-center gap-3">
            <div className="flex-1">
              <span className="eyebrow block mb-1">Output format</span>
              <TaskSelector value={taskType} onChange={setTaskType} />
            </div>
          </div>
        </div>

        {/* Text mode */}
        {(mode === "text" || projectId) && (
          <div className="surface p-6 mb-6" style={{ borderRadius: "var(--radius-lg)" }}>
            <p className="eyebrow mb-1">Object prompts</p>
            <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 16 }}>
              Describe each object type. Use commas for aliases (e.g. "car, sedan, SUV").
            </p>
            <div className="space-y-3">
              {classEntries.map((entry, i) => (
                <div key={i} className="flex gap-2 items-center">
                  <input
                    type="text"
                    value={entry.prompt}
                    onChange={(e) => updateClassEntry(i, "prompt", e.target.value)}
                    placeholder={i === 0 ? 'e.g. "car, sedan, SUV"' : "Prompts (comma-separated)"}
                    className="flex-1 rounded-lg px-4 py-2.5 text-sm outline-none"
                    style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                    onFocus={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
                    onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border-default)")}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && validEntries.length > 0) handleTextLabel();
                    }}
                  />
                  <input
                    type="text"
                    value={entry.name}
                    onChange={(e) => updateClassEntry(i, "name", e.target.value)}
                    placeholder="Class name"
                    title="Short name for training data"
                    className="w-32 rounded-lg px-3 py-2.5 text-sm outline-none"
                    style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}
                    onFocus={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
                    onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border-default)")}
                  />
                  {classEntries.length > 1 && (
                    <button
                      onClick={() => removeClassEntry(i)}
                      className="text-sm px-2"
                      style={{ color: "var(--danger)" }}
                    >
                      &times;
                    </button>
                  )}
                </div>
              ))}
              <div className="flex gap-3 pt-1">
                <button
                  onClick={addClassEntry}
                  className="px-4 py-2 rounded-lg text-sm"
                  style={{ border: "1px dashed var(--border-default)", color: "var(--text-muted)" }}
                >
                  + Add another class
                </button>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                      Threshold
                    </label>
                    <input
                      type="range"
                      min={0.05}
                      max={0.9}
                      step={0.05}
                      value={previewThreshold}
                      onChange={(e) => setPreviewThreshold(Number(e.target.value))}
                      className="w-24"
                      disabled={previewing}
                    />
                    <span className="text-xs font-mono w-8" style={{ color: "var(--text-secondary)" }}>
                      {previewThreshold.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <label className="text-[10px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                      Frames
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={16}
                      value={previewFrames}
                      onChange={(e) => setPreviewFrames(Math.max(1, Math.min(16, Number(e.target.value) || 8)))}
                      className="w-12 text-xs text-center rounded px-1 py-1"
                      style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                      disabled={previewing}
                    />
                  </div>
                  <button
                    onClick={handlePreview}
                    disabled={validEntries.length === 0 || previewing}
                    className="px-4 py-2.5 rounded-lg text-sm font-medium disabled:opacity-40 transition-colors"
                    style={{ border: "1px solid var(--accent)", color: "var(--accent)" }}
                  >
                    {previewing ? "Testing…" : preview ? "Re-test" : "Test Prompts"}
                  </button>
                </div>
                <button
                  onClick={handleTextLabel}
                  disabled={validEntries.length === 0 || !!isRunning}
                  className="px-6 py-2.5 text-white rounded-lg font-medium disabled:opacity-40 transition-colors"
                  style={{ backgroundColor: "var(--accent)" }}
                >
                  {validEntries.length > 1 ? `Find ${validEntries.length} Object Types` : "Find Objects"}
                </button>
              </div>

              {/* Preview results */}
              {preview && (
                <div className="mt-4 p-4 rounded-lg" style={{ backgroundColor: "var(--bg-inset)", border: "1px solid var(--border-subtle)" }}>
                  <div className="flex items-center justify-between mb-3">
                    <p className="eyebrow">
                      Preview: {preview.total_detections} detection{preview.total_detections !== 1 ? "s" : ""} across {preview.frames.length} frames
                    </p>
                    <button onClick={() => setPreview(null)} className="text-xs hover:underline" style={{ color: "var(--text-muted)" }}>
                      Dismiss
                    </button>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    {preview.frames.map((f) => (
                      <div key={f.frame_idx} className="relative rounded-lg overflow-hidden" style={{ border: "1px solid var(--border-default)" }}>
                        <img src={`data:image/jpeg;base64,${f.image_b64}`} alt={`Frame ${f.frame_idx}`} className="w-full aspect-video object-cover" />
                        <svg className="absolute inset-0 w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="none">
                          {f.detections.map((d, di) =>
                            d.polygon ? (
                              <polygon
                                key={di}
                                points={Array.from({ length: d.polygon.length / 2 }, (_, i) =>
                                  `${d.polygon![i * 2] * 100},${d.polygon![i * 2 + 1] * 100}`
                                ).join(" ")}
                                fill="rgba(37,99,235,0.2)"
                                stroke="var(--accent)"
                                strokeWidth={0.5}
                              />
                            ) : null
                          )}
                        </svg>
                        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent px-2 py-1">
                          <span style={{ fontSize: 10, color: "#fff", fontFamily: "var(--font-mono)" }}>
                            {f.detections.length} detection{f.detections.length !== 1 ? "s" : ""}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                  {preview.total_detections === 0 && (
                    <p className="text-center py-4" style={{ fontSize: 13, color: "var(--text-muted)" }}>
                      No detections found. Try different prompts or lower the threshold.
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Exemplar mode */}
        {mode === "exemplar" && !projectId && (
          <div className="mb-6 space-y-4">
            <div className="flex gap-3 items-center">
              <input
                type="text"
                value={className}
                onChange={(e) => setClassName(e.target.value)}
                placeholder="Class name"
                className="rounded-lg px-4 py-2 w-48"
                style={{ border: "1px solid var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}
              />
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Left-click = positive, Right-click = negative
              </span>
              <button onClick={() => setPoints([])} className="text-sm hover:underline" style={{ color: "var(--danger)" }}>
                Clear points
              </button>
            </div>

            {!selectedFrame && frames && (
              <div className="grid grid-cols-6 gap-2">
                {frames.map((f) => (
                  <img
                    key={f.id}
                    src={f.image_url}
                    className="rounded cursor-pointer transition-all"
                    style={{ border: "2px solid transparent" }}
                    onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
                    onMouseLeave={(e) => (e.currentTarget.style.borderColor = "transparent")}
                    onClick={() => setSelectedFrame(f)}
                  />
                ))}
              </div>
            )}

            {selectedFrame && (
              <>
                <ClickCanvas
                  imageUrl={selectedFrame.image_url}
                  width={selectedFrame.width || 640}
                  height={selectedFrame.height || 480}
                  points={points}
                  onAddPoint={(p) => setPoints([...points, p])}
                />
                <div className="flex gap-3">
                  <button
                    onClick={() => { setSelectedFrame(null); setPoints([]); }}
                    className="px-4 py-2 rounded-lg text-sm"
                    style={{ border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}
                  >
                    Back to frames
                  </button>
                  <button
                    onClick={handleExemplarLabel}
                    disabled={points.length === 0 || !!isRunning}
                    className="px-6 py-2 text-white rounded-lg disabled:opacity-50"
                    style={{ backgroundColor: "var(--accent)" }}
                  >
                    Label with {points.length} point(s)
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {/* Job progress */}
        {jobStatus && (() => {
          const statusColor = jobStatus.status === "completed" ? "var(--success)"
            : jobStatus.status === "failed" ? "var(--danger)" : "var(--accent)";
          const statusBg = jobStatus.status === "completed" ? "var(--success-soft)"
            : jobStatus.status === "failed" ? "var(--danger-soft)" : "var(--bg-inset)";
          return (
            <div className="rounded-lg p-4 mb-4" style={{ backgroundColor: statusBg, border: `1px solid ${statusColor}` }}>
              <div className="flex justify-between items-center mb-2">
                <span className="font-medium capitalize" style={{ color: statusColor }}>{jobStatus.status}</span>
                <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                  {jobStatus.processed_frames}/{jobStatus.total_frames} videos
                </span>
              </div>
              <div className="w-full rounded-full h-2" style={{ backgroundColor: "rgba(0,0,0,0.1)" }}>
                <div
                  className="h-2 rounded-full transition-all"
                  style={{ width: `${(jobStatus.progress || 0) * 100}%`, backgroundColor: statusColor }}
                />
              </div>
              {jobStatus.status === "completed" && (
                <div className="mt-3 flex gap-3">
                  <button
                    onClick={() => navigate(`/review/${jobStatus.job_id}`)}
                    className="px-4 py-2 text-white rounded-lg text-sm font-medium"
                    style={{ backgroundColor: "var(--success)" }}
                  >
                    Review Results
                  </button>
                  {jobStatus.result_url && (
                    <a
                      href={jobStatus.result_url}
                      className="px-4 py-2 rounded-lg text-sm"
                      style={{ border: "1px solid var(--success)", color: "var(--success)" }}
                    >
                      Download Dataset
                    </a>
                  )}
                </div>
              )}
              {jobStatus.status === "failed" && (
                <p className="mt-2 text-sm" style={{ color: "var(--danger)" }}>
                  {jobStatus.error_message}
                </p>
              )}
            </div>
          );
        })()}

        {error && <p className="text-sm" style={{ color: "var(--danger)" }}>{error}</p>}
      </div>
    </div>
  );
}
