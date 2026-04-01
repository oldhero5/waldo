import { useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  getJobStatus,
  listFrames,
  startExemplarLabeling,
  startLabeling,
  type ClassPrompt,
  type FrameOut,
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
      // Auto-fill name from prompt if name is empty
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
      const classPrompts: ClassPrompt[] = validEntries.map((e) => ({
        name: e.name.trim() || e.prompt.trim(),
        prompt: e.prompt.trim(),
      }));

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

  const isRunning =
    jobStatus &&
    !["completed", "failed"].includes(jobStatus.status);

  const title = projectId ? "Label Collection" : "Label Video";

  return (
    <div className="min-h-screen">

      <div className="max-w-3xl mx-auto mt-6 px-4 pb-12">
        {/* Header with video context */}
        <div className="bg-white rounded-xl border border-gray-200 p-6 mb-6">
          <h1 className="text-xl font-bold text-gray-900 mb-1">{title}</h1>

          {frames && frames.length > 0 && !projectId && (
            <div className="flex items-center gap-3 mt-3 p-3 bg-gray-50 rounded-lg">
              <img
                src={frames[0].image_url}
                alt="Video preview"
                className="w-24 h-16 object-cover rounded border border-gray-200"
              />
              <div>
                <p className="text-sm font-medium text-gray-700">{frames.length} frames extracted</p>
                {frames[0].width && frames[0].height && (
                  <p className="text-xs text-gray-400">{frames[0].width} &times; {frames[0].height}px</p>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Method selection */}
        <div className="bg-white rounded-xl border border-gray-200 p-6 mb-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">How do you want to label?</h2>
          {!projectId && (
            <div className="grid grid-cols-2 gap-3 mb-4">
              <button
                className={`p-4 rounded-lg border-2 text-left transition-all ${
                  mode === "text"
                    ? "border-blue-500 bg-blue-50"
                    : "border-gray-200 hover:border-gray-300"
                }`}
                onClick={() => setMode("text")}
              >
                <p className="font-medium text-sm text-gray-900">Describe with text</p>
                <p className="text-xs text-gray-500 mt-1">
                  Type what you're looking for and the AI will find it in every frame.
                </p>
              </button>
              <button
                className={`p-4 rounded-lg border-2 text-left transition-all ${
                  mode === "exemplar"
                    ? "border-blue-500 bg-blue-50"
                    : "border-gray-200 hover:border-gray-300"
                }`}
                onClick={() => setMode("exemplar")}
              >
                <p className="font-medium text-sm text-gray-900">Click on examples</p>
                <p className="text-xs text-gray-500 mt-1">
                  Point at objects in a frame and the AI will track them across the video.
                </p>
              </button>
            </div>
          )}

          <div className="flex items-center gap-3">
            <div className="flex-1">
              <label className="text-xs text-gray-500 mb-1 block">Output format</label>
              <TaskSelector value={taskType} onChange={setTaskType} />
            </div>
          </div>
        </div>

        {/* Text mode */}
        {(mode === "text" || projectId) && (
          <div className="bg-white rounded-xl border border-gray-200 p-6 mb-6">
            <h2 className="text-sm font-semibold text-gray-700 mb-1">What objects do you want to find?</h2>
            <p className="text-xs text-gray-400 mb-4">Describe each type of object. The AI will search every frame.</p>
            <div className="space-y-3">
              {classEntries.map((entry, i) => (
                <div key={i} className="flex gap-2 items-center">
                  <input
                    type="text"
                    value={entry.prompt}
                    onChange={(e) => updateClassEntry(i, "prompt", e.target.value)}
                    placeholder={i === 0 ? 'e.g. "person" or "red car"' : `Object type ${i + 1}`}
                    className="flex-1 border border-gray-200 rounded-lg px-4 py-2.5 focus:border-blue-400 focus:ring-1 focus:ring-blue-100 outline-none"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && validEntries.length > 0) handleTextLabel();
                    }}
                  />
                  <input
                    type="text"
                    value={entry.name}
                    onChange={(e) => updateClassEntry(i, "name", e.target.value)}
                    placeholder="Short label"
                    title="Short name for training data — auto-filled from your description"
                    className="w-32 border border-gray-200 rounded-lg px-3 py-2.5 text-sm focus:border-blue-400 focus:ring-1 focus:ring-blue-100 outline-none"
                  />
                  {classEntries.length > 1 && (
                    <button
                      onClick={() => removeClassEntry(i)}
                      className="text-red-400 hover:text-red-600 text-sm px-2"
                    >
                      &times;
                    </button>
                  )}
                </div>
              ))}
              <div className="flex gap-3 pt-1">
                <button
                  onClick={addClassEntry}
                  className="px-4 py-2 border border-dashed border-gray-300 rounded-lg text-sm text-gray-500 hover:bg-gray-50 hover:border-gray-400"
                >
                  + Add another object type
                </button>
                <button
                  onClick={handleTextLabel}
                  disabled={validEntries.length === 0 || !!isRunning}
                  className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium disabled:opacity-40 transition-colors"
                >
                  {validEntries.length > 1 ? `Find ${validEntries.length} Object Types` : "Find Objects"}
                </button>
              </div>
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
                className="border rounded-lg px-4 py-2 w-48"
              />
              <span className="text-sm text-gray-500">
                Left-click = positive, Right-click = negative
              </span>
              <button
                onClick={() => setPoints([])}
                className="text-sm text-red-600 hover:underline"
              >
                Clear points
              </button>
            </div>

            {/* Frame grid for selection */}
            {!selectedFrame && frames && (
              <div className="grid grid-cols-6 gap-2">
                {frames.map((f) => (
                  <img
                    key={f.id}
                    src={f.image_url}
                    className="rounded cursor-pointer hover:ring-2 ring-blue-500"
                    onClick={() => setSelectedFrame(f)}
                  />
                ))}
              </div>
            )}

            {/* Click canvas */}
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
                    onClick={() => {
                      setSelectedFrame(null);
                      setPoints([]);
                    }}
                    className="px-4 py-2 border rounded-lg text-sm"
                  >
                    Back to frames
                  </button>
                  <button
                    onClick={handleExemplarLabel}
                    disabled={points.length === 0 || !!isRunning}
                    className="px-6 py-2 bg-gray-900 text-white rounded-lg disabled:opacity-50"
                  >
                    Label with {points.length} point(s)
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {/* Job progress */}
        {jobStatus && (
          <div
            className={`rounded-lg p-4 mb-4 border ${
              jobStatus.status === "completed"
                ? "bg-green-50 border-green-200"
                : jobStatus.status === "failed"
                  ? "bg-red-50 border-red-200"
                  : "bg-gray-50 border-gray-200"
            }`}
          >
            <div className="flex justify-between items-center mb-2">
              <span className="font-medium capitalize">{jobStatus.status}</span>
              <span className="text-sm text-gray-500">
                {jobStatus.processed_frames}/{jobStatus.total_frames} frames
              </span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className={`h-2 rounded-full transition-all ${
                  jobStatus.status === "completed" ? "bg-green-600" : "bg-gray-900"
                }`}
                style={{ width: `${(jobStatus.progress || 0) * 100}%` }}
              />
            </div>
            {jobStatus.status === "completed" && (
              <div className="mt-3 flex gap-3">
                <button
                  onClick={() => navigate(`/review/${jobStatus.job_id}`)}
                  className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium"
                >
                  Review Results
                </button>
                {jobStatus.result_url && (
                  <a
                    href={jobStatus.result_url}
                    className="px-4 py-2 border border-green-300 text-green-700 rounded-lg text-sm hover:bg-green-100"
                  >
                    Download Dataset
                  </a>
                )}
              </div>
            )}
            {jobStatus.status === "failed" && (
              <p className="mt-2 text-red-600 text-sm">
                {jobStatus.error_message}
              </p>
            )}
          </div>
        )}

        {error && <p className="text-red-600 text-sm">{error}</p>}
      </div>
    </div>
  );
}
