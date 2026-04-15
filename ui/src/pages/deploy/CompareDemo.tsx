import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageIcon, Loader2, Trash2, VideoIcon, X, ZoomIn } from "lucide-react";
import {
  deleteComparison,
  getComparisonResult,
  listComparisons,
  saveComparison,
  startComparison,
  type CompareResultResponse,
  type DetectionOut,
  type FrameResultOut,
  type ModelOut,
} from "../../api";
import { drawDetections } from "./shared";
import { TrackTimeline } from "./TrackTimeline";

const SAM_SENTINEL = "__sam3.1__";

interface CompareResult {
  dets: DetectionOut[];
  frames?: FrameResultOut[];
  latency: number;
}

export function CompareDemo({ confThreshold, models }: { confThreshold: number; models: ModelOut[] | undefined }) {
  const [file, setFile] = useState<File | null>(null);
  const [modelA, setModelA] = useState("");
  const [modelB, setModelB] = useState(SAM_SENTINEL);
  const [samPrompts, setSamPrompts] = useState("person");
  const [resultA, setResultA] = useState<CompareResult | null>(null);
  const [resultB, setResultB] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState("");
  const [error, setError] = useState("");
  const [currentFrame, setCurrentFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [fullscreen, setFullscreen] = useState<"A" | "B" | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [, setVideoReady] = useState(false);
  const canvasARef = useRef<HTMLCanvasElement>(null);
  const canvasBRef = useRef<HTMLCanvasElement>(null);
  const fullscreenCanvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const emptySet = useRef(new Set<string>()).current;
  const playTimerRef = useRef<number | null>(null);
  const playIdxRef = useRef(0);
  const canvasDims = useRef<{ w: number; h: number } | null>(null);

  const queryClient = useQueryClient();
  const { data: history } = useQuery({ queryKey: ["comparisons"], queryFn: listComparisons });

  const isVideo = file?.type.startsWith("video/") ?? false;
  const previewUrl = useMemo(() => file ? URL.createObjectURL(file) : null, [file]);
  useEffect(() => { return () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }; }, [previewUrl]);

  const usingSam = modelA === SAM_SENTINEL || modelB === SAM_SENTINEL;
  const modelAName = modelA === SAM_SENTINEL ? "SAM 3.1 (teacher)" : models?.find((m) => m.id === modelA)?.name || "Model A";
  const modelBName = modelB === SAM_SENTINEL ? "SAM 3.1 (teacher)" : models?.find((m) => m.id === modelB)?.name || "Model B";

  const { timeline, timelineDetsA, timelineDetsB, totalFrames } = useMemo(() => {
    const frA = resultA?.frames ?? [];
    const frB = resultB?.frames ?? [];
    if (!frA.length && !frB.length) return { timeline: [], timelineDetsA: [], timelineDetsB: [], totalFrames: 0 };

    const tsSet = new Set<number>();
    for (const f of frA) tsSet.add(Math.round(f.timestamp_s * 1000) / 1000);
    for (const f of frB) tsSet.add(Math.round(f.timestamp_s * 1000) / 1000);
    const sorted = Array.from(tsSet).sort((a, b) => a - b);

    const findClosest = (frames: FrameResultOut[], ts: number): number => {
      if (!frames.length) return -1;
      let lo = 0, hi = frames.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (frames[mid].timestamp_s < ts) lo = mid + 1; else hi = mid;
      }
      if (lo > 0 && Math.abs(frames[lo - 1].timestamp_s - ts) < Math.abs(frames[lo].timestamp_s - ts)) lo--;
      return lo;
    };

    const tl = sorted.map((ts) => ({ timestamp_s: ts }));
    const dA: DetectionOut[][] = sorted.map((ts) => {
      const idx = findClosest(frA, ts);
      return idx >= 0 ? frA[idx].detections : [];
    });
    const dB: DetectionOut[][] = sorted.map((ts) => {
      const idx = findClosest(frB, ts);
      return idx >= 0 ? frB[idx].detections : [];
    });

    return { timeline: tl, timelineDetsA: dA, timelineDetsB: dB, totalFrames: sorted.length };
  }, [resultA, resultB]);

  const timelineFramesA = useMemo(() => timeline.map((e, i) => ({ frame_index: i, timestamp_s: e.timestamp_s, detections: timelineDetsA[i] || [] })), [timeline, timelineDetsA]);
  const timelineFramesB = useMemo(() => timeline.map((e, i) => ({ frame_index: i, timestamp_s: e.timestamp_s, detections: timelineDetsB[i] || [] })), [timeline, timelineDetsB]);
  const allClassesA = useMemo(() => new Set(timelineFramesA.flatMap((f) => f.detections.map((d) => d.class_name))), [timelineFramesA]);
  const allClassesB = useMemo(() => new Set(timelineFramesB.flatMap((f) => f.detections.map((d) => d.class_name))), [timelineFramesB]);

  const drawFrame = useCallback((idx: number) => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const vw = video.videoWidth, vh = video.videoHeight;

    if (!canvasDims.current) {
      const maxW = 960;
      const scale = Math.min(maxW / vw, 1);
      canvasDims.current = { w: Math.round(vw * scale), h: Math.round(vh * scale) };
    }
    const { w, h } = canvasDims.current;

    for (const [canvasRef, dets] of [[canvasARef, timelineDetsA[idx] || []], [canvasBRef, timelineDetsB[idx] || []]] as const) {
      const canvas = canvasRef.current;
      if (!canvas) continue;
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(video, 0, 0, w, h);
      drawDetections(ctx, dets, confThreshold, w, h, vw, vh);
    }
  }, [confThreshold, timelineDetsA, timelineDetsB]);

  const drawImageResult = useCallback((canvasRef: React.RefObject<HTMLCanvasElement | null>, dets: DetectionOut[]) => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !img.naturalWidth) return;
    if (canvas.width !== img.naturalWidth) { canvas.width = img.naturalWidth; canvas.height = img.naturalHeight; }
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    drawDetections(ctx, dets, confThreshold, img.naturalWidth, img.naturalHeight, img.naturalWidth, img.naturalHeight);
  }, [confThreshold]);

  useEffect(() => {
    if (!isVideo && resultA) drawImageResult(canvasARef, resultA.dets);
    if (!isVideo && resultB) drawImageResult(canvasBRef, resultB.dets);
  }, [resultA, resultB, drawImageResult, isVideo, confThreshold]);

  const seekToFrame = useCallback((idx: number) => {
    setCurrentFrame(idx);
    const entry = timeline[idx];
    if (!entry) return;
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = entry.timestamp_s;
    const draw = () => drawFrame(idx);
    video.addEventListener("seeked", draw, { once: true });
    setTimeout(draw, 80);
  }, [timeline, drawFrame]);

  const stopPlayback = useCallback(() => {
    if (playTimerRef.current != null) { clearTimeout(playTimerRef.current); playTimerRef.current = null; }
    setPlaying(false);
  }, []);

  const handlePlayPause = useCallback(() => {
    if (!isVideo || totalFrames < 2) return;
    if (playing) { stopPlayback(); return; }

    setPlaying(true);
    playIdxRef.current = currentFrame;

    const avgGapMs = totalFrames >= 2
      ? ((timeline[totalFrames - 1].timestamp_s - timeline[0].timestamp_s) / (totalFrames - 1)) * 1000
      : 200;
    const gapMs = Math.max(60, Math.min(avgGapMs, 1000));

    const step = () => {
      const next = playIdxRef.current + 1;
      if (next >= totalFrames) { stopPlayback(); return; }
      playIdxRef.current = next;
      setCurrentFrame(next);

      const video = videoRef.current;
      const entry = timeline[next];
      if (!video || !entry) { stopPlayback(); return; }
      video.currentTime = entry.timestamp_s;

      const onReady = () => {
        drawFrame(next);
        playTimerRef.current = window.setTimeout(step, gapMs);
      };
      video.addEventListener("seeked", onReady, { once: true });
      setTimeout(onReady, 80);
    };
    playTimerRef.current = window.setTimeout(step, 0);
  }, [isVideo, totalFrames, playing, currentFrame, timeline, stopPlayback, drawFrame]);

  useEffect(() => { return () => { if (playTimerRef.current != null) clearTimeout(playTimerRef.current); }; }, []);

  const drawFullscreen = useCallback(() => {
    const fsCanvas = fullscreenCanvasRef.current;
    if (!fsCanvas || !fullscreen) return;
    if (!isVideo) {
      const img = imgRef.current;
      const dets = fullscreen === "A" ? resultA?.dets : resultB?.dets;
      if (!img || !img.naturalWidth) return;
      if (fsCanvas.width !== img.naturalWidth) { fsCanvas.width = img.naturalWidth; fsCanvas.height = img.naturalHeight; }
      const ctx = fsCanvas.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      if (dets) drawDetections(ctx, dets, confThreshold, img.naturalWidth, img.naturalHeight, img.naturalWidth, img.naturalHeight);
    } else {
      const video = videoRef.current;
      if (!video || !video.videoWidth) return;
      const vw = video.videoWidth, vh = video.videoHeight;
      if (fsCanvas.width !== vw) { fsCanvas.width = vw; fsCanvas.height = vh; }
      const ctx = fsCanvas.getContext("2d")!;
      ctx.drawImage(video, 0, 0, vw, vh);
      const dets = fullscreen === "A" ? (timelineDetsA[currentFrame] || []) : (timelineDetsB[currentFrame] || []);
      drawDetections(ctx, dets, confThreshold, vw, vh, vw, vh);
    }
  }, [fullscreen, resultA, resultB, currentFrame, confThreshold, isVideo, timelineDetsA, timelineDetsB]);

  useEffect(() => { if (fullscreen) { const t = setTimeout(drawFullscreen, 50); return () => clearTimeout(t); } }, [fullscreen, currentFrame, drawFullscreen]);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullscreen(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  useEffect(() => { canvasDims.current = null; setVideoReady(false); }, [file]);

  const handleSave = async () => {
    if (!resultA || !resultB || !file) return;
    setSaving(true);
    try {
      const avgConfA = resultA.dets.length ? resultA.dets.reduce((s, d) => s + d.confidence, 0) / resultA.dets.length : null;
      const avgConfB = resultB.dets.length ? resultB.dets.reduce((s, d) => s + d.confidence, 0) / resultB.dets.length : null;
      await saveComparison({
        name: `${modelAName} vs ${modelBName}`,
        file_name: file.name,
        is_video: isVideo,
        sam_prompts: usingSam ? samPrompts.split(",").map((p) => p.trim()).filter(Boolean) : null,
        confidence_threshold: confThreshold,
        model_a_id: modelA === SAM_SENTINEL ? "sam3.1" : modelA,
        model_a_name: modelAName,
        model_a_detections: resultA.dets.filter((d) => d.confidence >= confThreshold).length,
        model_a_avg_confidence: avgConfA,
        model_a_latency_ms: resultA.latency,
        model_b_id: modelB === SAM_SENTINEL ? "sam3.1" : modelB,
        model_b_name: modelBName,
        model_b_detections: resultB.dets.filter((d) => d.confidence >= confThreshold).length,
        model_b_avg_confidence: avgConfB,
        model_b_latency_ms: resultB.latency,
        notes: null,
      });
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ["comparisons"] });
    } catch (e: any) {
      console.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteComparison = async (id: string) => {
    await deleteComparison(id);
    queryClient.invalidateQueries({ queryKey: ["comparisons"] });
  };

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(() => sessionStorage.getItem("waldo_compare_session"));

  const updateSessionId = useCallback((id: string | null) => {
    setSessionId(id);
    if (id) sessionStorage.setItem("waldo_compare_session", id);
    else { sessionStorage.removeItem("waldo_compare_session"); sessionStorage.removeItem("waldo_compare_meta"); }
  }, []);

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const loadResults = useCallback((data: CompareResultResponse) => {
    if (data.status !== "completed" || !data.results) return;
    const r = data.results;
    setResultA({
      dets: r.a.dets,
      frames: r.a.frames ?? undefined,
      latency: r.a.latency,
    });
    setResultB({
      dets: r.b.dets,
      frames: r.b.frames ?? undefined,
      latency: r.b.latency,
    });
    const errors = [r.a.error ? `Model A: ${r.a.error}` : "", r.b.error ? `Model B: ${r.b.error}` : ""].filter(Boolean).join(" | ");
    if (errors) setError(errors);
    setLoading(false);
    setLoadingMsg("");
  }, []);

  useEffect(() => {
    if (!sessionId || resultA || resultB) return;
    setLoading(true);
    setLoadingMsg("Comparison running in background...");

    pollRef.current = setInterval(async () => {
      try {
        const data = await getComparisonResult(sessionId);
        if (data.status === "completed") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          loadResults(data);
          updateSessionId(null);
        }
      } catch {
        // ignore
      }
    }, 2000);

    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCompare = async () => {
    if (!file || !modelA || !modelB) return;
    setLoading(true);
    setError("");
    setResultA(null);
    setResultB(null);
    setCurrentFrame(0);
    setSaved(false);
    setLoadingMsg("Uploading and starting comparison...");

    const prompts = usingSam ? samPrompts.split(",").map((p) => p.trim()).filter(Boolean) : undefined;
    if (usingSam && (!prompts || !prompts.length)) {
      setError("Enter at least one SAM prompt");
      setLoading(false);
      setLoadingMsg("");
      return;
    }

    try {
      const session = await startComparison(
        file,
        modelA === SAM_SENTINEL ? "sam3.1" : modelA,
        modelB === SAM_SENTINEL ? "sam3.1" : modelB,
        confThreshold,
        prompts,
      );
      updateSessionId(session.session_id);
      sessionStorage.setItem("waldo_compare_meta", JSON.stringify({
        modelA: modelAName, modelB: modelBName, fileName: file.name,
      }));
      setLoadingMsg("Comparison running — you can navigate away. Results will appear when ready.");

      pollRef.current = setInterval(async () => {
        try {
          const data = await getComparisonResult(session.session_id);
          if (data.status === "completed") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            loadResults(data);
            updateSessionId(null);
          }
        } catch {
          // ignore
        }
      }, 2000);
    } catch (e: any) {
      setError(e.message);
      setLoading(false);
      setLoadingMsg("");
    }
  };

  return (
    <div>
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <label className="text-[10px] uppercase tracking-wide mb-1 block" style={{ color: "var(--text-muted)" }}>Model A (student)</label>
          <select value={modelA} onChange={(e) => setModelA(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg border" style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-surface)" }}>
            <option value="">Select model...</option>
            {models?.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.model_variant}){m.alias ? ` [${m.alias}]` : ""}</option>)}
            <option value={SAM_SENTINEL}>SAM 3.1 (teacher)</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] uppercase tracking-wide mb-1 block" style={{ color: "var(--text-muted)" }}>Model B (baseline)</label>
          <select value={modelB} onChange={(e) => setModelB(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg border" style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-surface)" }}>
            <option value="">Select model...</option>
            <option value={SAM_SENTINEL}>SAM 3.1 (teacher)</option>
            {models?.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.model_variant}){m.alias ? ` [${m.alias}]` : ""}</option>)}
          </select>
        </div>
      </div>

      {usingSam && (
        <div className="mb-4">
          <label className="text-[10px] uppercase tracking-wide mb-1 block" style={{ color: "var(--text-muted)" }}>SAM 3.1 Text Prompts (comma-separated)</label>
          <input value={samPrompts} onChange={(e) => setSamPrompts(e.target.value)} placeholder="person, car, bicycle"
            className="w-full px-3 py-2 text-sm rounded-lg border" style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-surface)" }} />
          <p className="text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>
            SAM 3.1 uses text prompts to find objects. Enter the same classes your YOLO model was trained on.
          </p>
        </div>
      )}

      <div className="mb-4">
        <label
          className="flex flex-col items-center justify-center p-6 border-2 border-dashed rounded-xl cursor-pointer transition-colors"
          style={{ borderColor: file ? "var(--accent)" : "var(--border-default)", backgroundColor: file ? "color-mix(in srgb, var(--accent) 5%, transparent 95%)" : "transparent" }}
          onDragOver={(e) => { e.preventDefault(); e.currentTarget.style.borderColor = "var(--accent)"; }}
          onDragLeave={(e) => { e.currentTarget.style.borderColor = file ? "var(--accent)" : "var(--border-default)"; }}
          onDrop={(e) => { e.preventDefault(); setFile(e.dataTransfer.files?.[0] || null); setResultA(null); setResultB(null); }}
        >
          <input type="file" accept="image/*,video/*" className="hidden" onChange={(e) => { setFile(e.target.files?.[0] || null); setResultA(null); setResultB(null); }} />
          {file ? (
            <div className="flex items-center gap-3">
              {isVideo ? <VideoIcon size={20} style={{ color: "var(--accent)" }} /> : <ImageIcon size={20} style={{ color: "var(--accent)" }} />}
              <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{file.name}</span>
              <span className="text-xs px-2 py-0.5 rounded-full" style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-muted)" }}>
                {isVideo ? "Video" : "Image"}
              </span>
              <span className="text-xs" style={{ color: "var(--accent)" }}>Change</span>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-1">
                <ImageIcon size={20} style={{ color: "var(--text-muted)" }} />
                <VideoIcon size={20} style={{ color: "var(--text-muted)" }} />
              </div>
              <span className="text-sm" style={{ color: "var(--text-secondary)" }}>Drop an image or video here, or click to browse</span>
              <span className="text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>Supports JPG, PNG, MP4, MOV</span>
            </>
          )}
        </label>
      </div>

      <div className="flex items-center gap-3 mb-4">
        {file && modelA && modelB && (
          <button onClick={handleCompare} disabled={loading}
            className="px-6 py-2.5 text-white rounded-lg text-sm font-medium disabled:opacity-40" style={{ backgroundColor: "var(--accent)" }}>
            {loading ? "Comparing..." : "Compare Models"}
          </button>
        )}
        {!modelA && file && (
          <span className="text-xs" style={{ color: "var(--warning)" }}>Select a trained model for Model A</span>
        )}
      </div>

      {loading && loadingMsg && (
        <div className="surface p-4 mb-4 flex items-center justify-between" style={{ border: "1px solid var(--accent)" }}>
          <div className="flex items-center gap-3">
            <Loader2 size={18} className="animate-spin" style={{ color: "var(--accent)" }} />
            <div>
              <span className="text-sm font-medium block" style={{ color: "var(--text-primary)" }}>{loadingMsg}</span>
              {sessionId && (
                <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                  You can navigate to other pages — results will be here when you come back.
                </span>
              )}
            </div>
          </div>
          {sessionId && (
            <span className="text-[10px] px-2 py-1 rounded-full" style={{ backgroundColor: "var(--accent-soft)", color: "var(--accent)" }}>
              Background
            </span>
          )}
        </div>
      )}

      {error && <p className="text-sm mb-3" style={{ color: "var(--danger)" }}>{error}</p>}

      {previewUrl && !isVideo && <img ref={imgRef} src={previewUrl} alt="" className="hidden" onLoad={() => {
        if (resultA) drawImageResult(canvasARef, resultA.dets);
        if (resultB) drawImageResult(canvasBRef, resultB.dets);
      }} />}

      {previewUrl && isVideo && (
        <video ref={videoRef} src={previewUrl} muted playsInline preload="auto"
          style={{ position: "absolute", width: 1, height: 1, opacity: 0, pointerEvents: "none" }}
          onLoadedData={() => { setVideoReady(true); if (totalFrames > 0) seekToFrame(0); }} />
      )}

      {(resultA || resultB) && (
        <>
          <div className="grid grid-cols-2 gap-4">
            {([
              { label: modelAName, result: resultA, canvasRef: canvasARef, side: "A" as const, tlFrames: timelineFramesA, allClasses: allClassesA },
              { label: modelBName, result: resultB, canvasRef: canvasBRef, side: "B" as const, tlFrames: timelineFramesB, allClasses: allClassesB },
            ]).map((s) => {
              const detsAtFrame = isVideo
                ? (s.side === "A" ? timelineDetsA[currentFrame] : timelineDetsB[currentFrame]) || []
                : s.result?.dets ?? [];
              const visibleDets = detsAtFrame.filter((d) => d.confidence >= confThreshold);
              return (
                <div key={s.side}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{s.label}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
                        {visibleDets.length} dets
                        {s.result && ` \u00b7 ${s.result.latency.toFixed(0)}ms`}
                      </span>
                      <button
                        onClick={() => setFullscreen(s.side)}
                        className="p-1 rounded"
                        title="Fullscreen"
                        style={{ color: "var(--text-muted)" }}
                      >
                        <ZoomIn size={14} />
                      </button>
                    </div>
                  </div>
                  <canvas
                    ref={s.canvasRef}
                    className="w-full rounded-lg cursor-pointer"
                    style={{ border: "1px solid var(--border-subtle)" }}
                    onClick={() => setFullscreen(s.side)}
                  />
                  {isVideo && totalFrames > 0 && (
                    <TrackTimeline
                      frames={s.tlFrames}
                      currentFrame={currentFrame}
                      confThreshold={confThreshold}
                      classFilter={s.allClasses}
                      onSeek={seekToFrame}
                      flaggedSet={emptySet}
                    />
                  )}
                  {visibleDets.length > 0 && (
                    <div className="mt-1 flex items-center gap-1">
                      <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>Avg conf:</span>
                      <span className="text-xs font-mono font-medium" style={{ color: "var(--text-primary)" }}>
                        {(visibleDets.reduce((acc, d) => acc + d.confidence, 0) / visibleDets.length * 100).toFixed(1)}%
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {isVideo && totalFrames > 0 && (
            <div className="surface p-3 mt-3">
              <div className="flex items-center gap-2 mb-2">
                <button onClick={handlePlayPause}
                  className="px-3 py-1.5 text-white rounded-lg text-sm min-w-[70px] font-medium"
                  style={{ backgroundColor: "var(--accent)" }}>
                  {playing ? "Pause" : "Play"}
                </button>
                <button onClick={() => seekToFrame(Math.max(0, currentFrame - 1))} disabled={currentFrame === 0}
                  className="px-2 py-1.5 text-sm rounded border disabled:opacity-30" style={{ backgroundColor: "var(--bg-surface)", borderColor: "var(--border-default)" }}>&larr;</button>
                <button onClick={() => seekToFrame(Math.min(totalFrames - 1, currentFrame + 1))} disabled={currentFrame >= totalFrames - 1}
                  className="px-2 py-1.5 text-sm rounded border disabled:opacity-30" style={{ backgroundColor: "var(--bg-surface)", borderColor: "var(--border-default)" }}>&rarr;</button>
                <span className="text-sm font-mono ml-auto" style={{ color: "var(--text-muted)" }}>
                  {currentFrame + 1}/{totalFrames}
                </span>
                {timeline[currentFrame] && (
                  <span className="text-sm font-mono" style={{ color: "var(--text-primary)" }}>
                    {timeline[currentFrame].timestamp_s.toFixed(2)}s
                  </span>
                )}
              </div>
              <input type="range" min={0} max={totalFrames - 1} value={currentFrame}
                onInput={(e) => { stopPlayback(); seekToFrame(Number((e.target as HTMLInputElement).value)); }}
                onChange={() => {}}
                className="w-full h-2 appearance-none rounded-full cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                  [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-600 [&::-webkit-slider-thumb]:cursor-grab"
                style={{ backgroundColor: "var(--bg-inset)" }}
              />
            </div>
          )}

          <div className="surface p-4 mt-4">
            <h4 className="text-sm font-semibold mb-2" style={{ color: "var(--text-primary)" }}>
              {isVideo ? "Aggregate Comparison" : "Comparison"}
            </h4>
            <div className="grid grid-cols-3 gap-4 text-xs">
              {[
                {
                  label: isVideo ? "Total Detections" : "Detections",
                  a: resultA ? resultA.dets.filter((d) => d.confidence >= confThreshold).length : 0,
                  b: resultB ? resultB.dets.filter((d) => d.confidence >= confThreshold).length : 0,
                  fmt: (v: number) => v.toLocaleString(),
                },
                {
                  label: "Avg Confidence",
                  a: resultA?.dets.length ? resultA.dets.reduce((acc, d) => acc + d.confidence, 0) / resultA.dets.length : 0,
                  b: resultB?.dets.length ? resultB.dets.reduce((acc, d) => acc + d.confidence, 0) / resultB.dets.length : 0,
                  fmt: (v: number) => `${(v * 100).toFixed(1)}%`,
                },
                {
                  label: "Latency",
                  a: resultA?.latency ?? 0,
                  b: resultB?.latency ?? 0,
                  fmt: (v: number) => v > 1000 ? `${(v / 1000).toFixed(1)}s` : `${v.toFixed(0)}ms`,
                },
              ].map((metric) => {
                const diff = metric.a - metric.b;
                const better = metric.label === "Latency" ? diff < 0 : diff > 0;
                return (
                  <div key={metric.label} className="text-center">
                    <span className="block mb-1" style={{ color: "var(--text-muted)" }}>{metric.label}</span>
                    <span className="block font-mono" style={{ color: "var(--text-primary)" }}>
                      {metric.fmt(metric.a)} vs {metric.fmt(metric.b)}
                    </span>
                    {diff !== 0 && resultA && resultB && (
                      <span className="text-[10px] font-medium" style={{ color: better ? "var(--success)" : "var(--danger)" }}>
                        {modelAName} {better ? "wins" : "loses"}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="flex items-center gap-2 mt-4 pt-3" style={{ borderTop: "1px solid var(--border-subtle)" }}>
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-1.5 text-sm rounded-lg font-medium"
                style={{ backgroundColor: "var(--accent-soft)", color: "var(--accent)" }}
              >
                {saving ? "Saving..." : saved ? "Saved" : "Save to History"}
              </button>
              {saved && <span className="text-xs" style={{ color: "var(--success)" }}>Comparison saved for future reference</span>}
            </div>
          </div>
        </>
      )}

      {history && history.length > 0 && (
        <div className="surface p-5 mt-6">
          <h4 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>Comparison History</h4>
          <div className="space-y-2">
            {history.map((h) => {
              const aWins = h.model_a_detections > h.model_b_detections;
              const confA = h.model_a_avg_confidence ?? 0;
              const confB = h.model_b_avg_confidence ?? 0;
              return (
                <div key={h.id} className="flex items-center justify-between py-2.5 px-3 rounded-lg" style={{ backgroundColor: "var(--bg-inset)" }}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium truncate" style={{ color: "var(--text-primary)" }}>{h.name}</span>
                      {h.is_video && <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ backgroundColor: "var(--accent-soft)", color: "var(--accent)" }}>Video</span>}
                    </div>
                    <div className="flex items-center gap-3 text-[10px] mt-0.5" style={{ color: "var(--text-muted)" }}>
                      <span>{h.model_a_name} vs {h.model_b_name}</span>
                      <span>{h.file_name}</span>
                      <span>{new Date(h.created_at).toLocaleDateString()}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 text-xs shrink-0 ml-3">
                    <div className="text-center">
                      <span className="block font-mono" style={{ color: aWins ? "var(--success)" : "var(--text-muted)" }}>{h.model_a_detections}</span>
                      <span className="text-[9px]" style={{ color: "var(--text-muted)" }}>dets A</span>
                    </div>
                    <div className="text-center">
                      <span className="block font-mono" style={{ color: !aWins ? "var(--success)" : "var(--text-muted)" }}>{h.model_b_detections}</span>
                      <span className="text-[9px]" style={{ color: "var(--text-muted)" }}>dets B</span>
                    </div>
                    <div className="text-center">
                      <span className="block font-mono">{(confA * 100).toFixed(0)}%</span>
                      <span className="text-[9px]" style={{ color: "var(--text-muted)" }}>conf A</span>
                    </div>
                    <div className="text-center">
                      <span className="block font-mono">{(confB * 100).toFixed(0)}%</span>
                      <span className="text-[9px]" style={{ color: "var(--text-muted)" }}>conf B</span>
                    </div>
                    <button onClick={() => handleDeleteComparison(h.id)} className="p-1 rounded" title="Delete">
                      <Trash2 size={12} style={{ color: "var(--text-muted)" }} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {fullscreen && (
        <div
          className="fixed inset-0 z-[9999] flex flex-col"
          style={{ backgroundColor: "#000" }}
          onClick={() => setFullscreen(null)}
        >
          <div className="flex items-center justify-between px-4 py-2" style={{ backgroundColor: "rgba(0,0,0,0.8)" }}>
            <span className="text-white text-sm font-semibold">
              {fullscreen === "A" ? modelAName : modelBName}
            </span>
            <button onClick={() => setFullscreen(null)} className="text-white p-1">
              <X size={20} />
            </button>
          </div>
          <div className="flex-1 flex items-center justify-center overflow-auto p-4" onClick={(e) => e.stopPropagation()}>
            <canvas
              ref={fullscreenCanvasRef}
              style={{ maxWidth: "100%", maxHeight: "calc(100vh - 140px)", objectFit: "contain" }}
            />
          </div>
          {isVideo && totalFrames > 0 && (
            <div className="px-4 pb-4" onClick={(e) => e.stopPropagation()}>
              {(() => {
                const fsTlFrames = fullscreen === "A" ? timelineFramesA : timelineFramesB;
                const fsAllClasses = fullscreen === "A" ? allClassesA : allClassesB;
                return (
                  <TrackTimeline frames={fsTlFrames} currentFrame={currentFrame} confThreshold={confThreshold}
                    classFilter={fsAllClasses} onSeek={(i) => { seekToFrame(i); setTimeout(drawFullscreen, 160); }} flaggedSet={emptySet} />
                );
              })()}
              <div className="flex items-center gap-2 mt-2">
                <button onClick={(e) => { e.stopPropagation(); handlePlayPause(); }}
                  className="px-3 py-1.5 text-white rounded-lg text-sm min-w-[70px] font-medium bg-blue-600">
                  {playing ? "Pause" : "Play"}
                </button>
                <input type="range" min={0} max={totalFrames - 1} value={currentFrame}
                  onInput={(e) => { e.stopPropagation(); stopPlayback(); seekToFrame(Number((e.target as HTMLInputElement).value)); drawFullscreen(); }}
                  onChange={() => {}}
                  className="flex-1 h-2 appearance-none rounded-full cursor-pointer bg-gray-700
                    [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                    [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-500 [&::-webkit-slider-thumb]:cursor-grab"
                />
                <span className="text-white text-xs font-mono">{currentFrame + 1}/{totalFrames}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {!file && !loading && !resultA && (
        <p className="text-xs text-center mt-2" style={{ color: "var(--text-muted)" }}>
          Default baseline is SAM 3.1 (teacher) via mlx-vlm. Works with both images and video.
        </p>
      )}
    </div>
  );
}
