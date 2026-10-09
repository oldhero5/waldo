import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Loader2, VideoIcon } from "lucide-react";
import {
  predictVideo,
  streamPredictFrames,
  type DetectionOut,
  type FrameResultOut,
} from "../../api";
import { applyZoomPan, drawDetections, trackColor, useZoomPan } from "./shared";
import { ZoomIndicator } from "./ZoomIndicator";
import { assessedFrameAt, compatibleSourceSize, seekDecodedVideo, sourceFrameDuration, watchVideoPresentation } from "../../lib/videoPresentation";
import { TrackTimeline } from "./TrackTimeline";

export function VideoDemo({ confThreshold, classFilter, classFilterArr, modelId }: {
  confThreshold: number; classFilter: Set<string>; classFilterArr: string[];
  modelId: string | null;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [frames, setFrames] = useState<FrameResultOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [videoReady, setVideoReady] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);
  const [recovering, setRecovering] = useState(false);
  const [flagged, setFlagged] = useState<Set<string>>(new Set());
  const [feedbackMsg, setFeedbackMsg] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const presentedTime = useRef(0);
  const [assessedFrame, setAssessedFrame] = useState<number | null>(null);
  const [dimensionMismatch, setDimensionMismatch] = useState(false);
  const requestRevision = useRef({ revision: 0 });
  const captureRequest = useRef<AbortController | null>(null);
  const feedbackInFlight = useRef(false);
  const predictionSource = useRef<{ modelId: string | null; filename: string } | null>(null);
  const wsCleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!file) { setVideoUrl(""); setVideoReady(false); return; }
    const url = URL.createObjectURL(file);
    setVideoUrl(url);
    setVideoReady(false);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    const lifetime = requestRevision.current;
    return () => { lifetime.revision++; wsCleanupRef.current?.(); captureRequest.current?.abort(); };
  }, []);

  const handlePredict = useCallback(async () => {
    if (!file) return;
    const revision = ++requestRevision.current.revision;
    predictionSource.current = { modelId, filename: file.name };
    videoRef.current?.pause();
    captureRequest.current?.abort();
    wsCleanupRef.current?.();
    setLoading(true);
    setError("");
    setFrames([]);
    setCurrentFrame(0);
    setPlaying(false);
    setProgress(null);
    setRecovering(false);
    setFlagged(new Set());
    setFeedbackMsg("");
    try {
      const result = await predictVideo(file, confThreshold, classFilterArr.length > 0 ? classFilterArr : undefined, modelId || undefined);
      if (revision !== requestRevision.current.revision) return;
      if ("frames" in result) {
        setFrames(result.frames);
      } else {
        setProgress({ current: 0, total: result.frame_count });
        const streamedFrames = new Map<number, FrameResultOut>();
        wsCleanupRef.current = streamPredictFrames(
          result.session_id,
          (frame) => {
            if (revision !== requestRevision.current.revision) return;
            streamedFrames.set(frame.frame_index, frame);
            setProgress({ current: streamedFrames.size, total: result.frame_count });
          },
          (_, completedFrames) => {
            if (revision !== requestRevision.current.revision) return;
            setFrames(completedFrames || [...streamedFrames.values()].sort((a, b) => a.timestamp_s - b.timestamp_s));
            setLoading(false); setProgress(null); setRecovering(false);
          },
          (err) => {
            if (revision !== requestRevision.current.revision) return;
            if (streamedFrames.size > 0) setFrames([...streamedFrames.values()].sort((a, b) => a.timestamp_s - b.timestamp_s));
            setError(err); setLoading(false); setProgress(null); setRecovering(false);
          },
          () => { if (revision === requestRevision.current.revision) setRecovering(true); },
        );
        return;
      }
    } catch (e: unknown) {
      if (revision !== requestRevision.current.revision) return;
      setError((e instanceof Error ? e.message : String(e)));
    }
    setLoading(false);
  }, [file, confThreshold, classFilterArr, modelId]);

  const drawAtTime = useCallback((time: number) => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !frames.length || !video.videoWidth || video.seeking || video.readyState < 2) return;
    presentedTime.current = time;
    const frameIdx = assessedFrameAt(frames, time);
    setAssessedFrame(frameIdx < 0 ? null : frameIdx);
    if (frameIdx >= 0) setCurrentFrame(frameIdx);

    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const maxW = 960;
    const scale = Math.min(maxW / vw, 1);
    const width = Math.round(vw * scale), height = Math.round(vh * scale);
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }

    const ctx = canvas.getContext("2d")!;
    ctx.save();
    applyZoomPan(ctx, zpRef.current.zoom, zpRef.current.panX, zpRef.current.panY);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    const fr = frames[frameIdx];
    const compatible = !fr || compatibleSourceSize(fr, vw, vh);
    setDimensionMismatch(!compatible);
    if (fr && compatible) {
      const sourceWidth = fr.source_width || vw, sourceHeight = fr.source_height || vh;
      const visibleDets = fr.detections.filter((d) => {
        const key = `${frameIdx}-${d.track_id}`;
        return !flagged.has(key);
      });
      drawDetections(ctx, visibleDets, confThreshold, canvas.width, canvas.height, sourceWidth, sourceHeight, classFilter, zpRef.current.zoom);

      const flaggedDets = fr.detections.filter((d) => {
        const key = `${frameIdx}-${d.track_id}`;
        return flagged.has(key) && d.confidence >= confThreshold && classFilter.has(d.class_name);
      });
      for (const det of flaggedDets) {
        const scaleX = canvas.width / sourceWidth;
        const scaleY = canvas.height / sourceHeight;
        const [x1, y1, x2, y2] = det.bbox.map((v, i) => v * (i % 2 === 0 ? scaleX : scaleY));
        ctx.strokeStyle = "#ef4444";
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 4]);
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
        ctx.beginPath();
        ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
        ctx.moveTo(x2, y1); ctx.lineTo(x1, y2);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
    ctx.restore();
  }, [frames, confThreshold, classFilter, flagged]);

  const drawFrame = useCallback(() => { drawAtTime(presentedTime.current); }, [drawAtTime]);
  const { zoom, panX, panY, reset } = useZoomPan(canvasRef, drawFrame, frames.length > 0);
  const zpRef = useRef({ zoom, panX, panY });
  useLayoutEffect(() => { zpRef.current = { zoom, panX, panY }; }, [zoom, panX, panY]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !videoReady || !frames.length) return;
    return watchVideoPresentation(video, drawAtTime);
  }, [videoReady, frames.length, videoUrl, drawAtTime]);

  const seekToFrame = useCallback((idx: number) => {
    const video = videoRef.current;
    if (!video || !frames[idx]) return;
    video.pause();
    setPlaying(false);
    setCurrentFrame(idx);
    setAssessedFrame(null);
    // Seek inside the source frame's interval to avoid floating-point boundary rounding.
    video.currentTime = frames[idx].timestamp_s + Math.min(sourceFrameDuration(frames, idx) / 4, 0.005);
  }, [frames]);

  const handlePlay = async () => {
    const video = videoRef.current;
    if (!video) return;
    if (!video.paused) video.pause();
    else {
      try { await video.play(); } catch { setError("Video playback could not start"); }
    }
  };

  const toggleFlag = (frameIdx: number, det: DetectionOut) => {
    const key = `${frameIdx}-${det.track_id}`;
    setFlagged((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const captureFrameB64 = useCallback(async (frame: FrameResultOut): Promise<string | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return null;
    video.pause();
    const controller = new AbortController();
    captureRequest.current?.abort();
    captureRequest.current = controller;
    if (!compatibleSourceSize(frame, video.videoWidth, video.videoHeight)) throw new Error("Cannot capture feedback with incompatible source dimensions");
    await seekDecodedVideo(video, frame.timestamp_s + Math.min((frame.frame_duration_s || 0) / 4, 0.005), controller.signal);
    const tmp = document.createElement("canvas");
    tmp.width = frame.source_width || video.videoWidth; tmp.height = frame.source_height || video.videoHeight;
    const ctx = tmp.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(video, 0, 0, tmp.width, tmp.height);
    return tmp.toDataURL("image/jpeg", 0.85).split(",")[1];
  }, []);

  const handleSubmitFeedback = useCallback(async () => {
    if (flagged.size === 0 || feedbackInFlight.current) return;
    const revision = requestRevision.current.revision;
    const source = predictionSource.current;
    feedbackInFlight.current = true;
    setFeedbackMsg("Capturing frames...");
    const items: import("../../api").FeedbackIn[] = [];

    const byFrame = new Map<number, { fr: typeof frames[0]; dets: typeof frames[0]["detections"] }>();
    for (const key of flagged) {
      const [fiStr, tidStr] = key.split("-");
      const fi = Number(fiStr);
      const tid = tidStr !== "null" ? Number(tidStr) : null;
      const fr = frames[fi];
      if (!fr) continue;
      const det = fr.detections.find((d) => d.track_id === tid);
      if (!det) continue;
      if (!byFrame.has(fi)) byFrame.set(fi, { fr, dets: [] });
      byFrame.get(fi)!.dets.push(det);
    }

    try {
      for (const [, { fr, dets }] of byFrame) {
        const b64 = await captureFrameB64(fr);
        if (revision !== requestRevision.current.revision) return;
        for (const det of dets) {
          items.push({
            model_id: source?.modelId || undefined,
            class_name: det.class_name,
            bbox: det.bbox,
            polygon: det.mask,
            confidence: det.confidence,
            track_id: det.track_id,
            frame_index: fr.frame_index,
            timestamp_s: fr.timestamp_s,
            feedback_type: "false_positive",
            source_filename: source?.filename,
            frame_image_b64: b64 || undefined,
          });
        }
      }
      const { submitFeedbackBatch } = await import("../../api");
      if (revision !== requestRevision.current.revision) return;
      setFeedbackMsg("Submitting...");
      await submitFeedbackBatch(items);
      if (revision !== requestRevision.current.revision) return;
      setFeedbackMsg(`${items.length} false positive${items.length !== 1 ? "s" : ""} saved. These become negative examples in your next training run.`);
    } catch (e: unknown) {
      if (revision === requestRevision.current.revision) setFeedbackMsg(`Error: ${(e instanceof Error ? e.message : String(e))}`);
    } finally {
      feedbackInFlight.current = false;
    }
  }, [flagged, frames, captureFrameB64]);

  const stats = useMemo(() => {
    const uniqueTracks = new Set<number>();
    let totalDets = 0;
    for (const f of frames) {
      for (const d of f.detections) {
        if (d.track_id != null) uniqueTracks.add(d.track_id);
        if (d.confidence >= confThreshold && classFilter.has(d.class_name)) totalDets++;
      }
    }
    return { uniqueTracks: uniqueTracks.size, totalDets };
  }, [frames, confThreshold, classFilter]);

  const current = assessedFrame == null ? undefined : frames[assessedFrame];
  const visibleDets = current?.detections.filter(
    (d) => d.confidence >= confThreshold && classFilter.has(d.class_name)
  ) || [];

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <label className="flex items-center gap-2 px-4 py-2 border-2 border-dashed rounded-lg text-sm cursor-pointer" style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
          <VideoIcon size={16} />
          Choose Video
          <input type="file" accept="video/*" className="hidden" onChange={(e) => {
            requestRevision.current.revision++;
            wsCleanupRef.current?.(); captureRequest.current?.abort(); videoRef.current?.pause();
            setLoading(false); setPlaying(false); setAssessedFrame(null);
            setFile(e.target.files?.[0] || null);
            setFrames([]); setCurrentFrame(0); setFlagged(new Set()); setFeedbackMsg(""); reset();
          }} />
        </label>
        {file && (
          <button onClick={handlePredict} disabled={loading} className="px-4 py-2 bg-accent text-on-accent hover:bg-accent-hover rounded-lg text-sm disabled:opacity-40">
            {loading ? "Processing..." : "Track Objects"}
          </button>
        )}
      </div>

      {error && <p className="text-sm mb-3" style={{ color: "var(--danger)" }}>{error}</p>}
      {loading && (
        <div className="surface p-4 mb-4">
          <div className="flex items-center gap-3 mb-2">
            <Loader2 size={18} className="animate-spin shrink-0" style={{ color: "var(--text-secondary)" }} />
            <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
              {recovering ? "Live updates interrupted; checking server status..." : progress && progress.total > 0 ? "Processing video frames..." : "Sending video to model..."}
            </span>
          </div>
          {progress && progress.total > 0 ? (
            <div>
              <div className="flex items-center gap-3">
                <div className="flex-1 rounded-full h-2.5 overflow-hidden" style={{ backgroundColor: "var(--bg-inset)" }}>
                  <div className="bg-accent text-on-accent h-full rounded-full transition-all duration-300"
                    style={{ width: `${Math.round((progress.current / progress.total) * 100)}%` }} />
                </div>
                <span className="font-mono text-sm w-32 text-right shrink-0" style={{ color: "var(--text-secondary)" }}>
                  {progress.current}/{progress.total} frames
                </span>
              </div>
              <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{Math.round((progress.current / progress.total) * 100)}% complete</p>
            </div>
          ) : (
            <div className="w-full rounded-full h-2.5 overflow-hidden" style={{ backgroundColor: "var(--bg-inset)" }}>
              <div className="bg-accent text-on-accent h-full rounded-full animate-pulse w-1/3" />
            </div>
          )}
        </div>
      )}

      {videoUrl && (
        <video ref={videoRef} src={videoUrl} className="hidden" muted playsInline preload="auto"
          onLoadedData={() => setVideoReady(true)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
      )}

      {frames.length > 0 && (
        <div>
          {frames.some((frame) => frame.timestamp_method && frame.timestamp_method !== "source_pts") && <p className="text-xs mb-2" style={{ color: "var(--warning)" }}>Source timestamps are approximate; exact playback masks are unavailable.</p>}
          <p className="text-xs mb-2" style={{ color: "var(--text-muted)" }}>Masks appear only on assessed frames; intermediate video frames have no inferred mask.</p>
          {dimensionMismatch && <p role="alert" className="text-sm mb-2" style={{ color: "var(--warning)" }}>Overlay dimensions do not match the decoded video. Masks are hidden.</p>}
          <div className="relative inline-block">
            <canvas ref={canvasRef} className="rounded-lg mb-2 bg-black"
              style={{ maxWidth: "100%", cursor: zoom > 1 ? "grab" : "default", border: "1px solid var(--border-subtle)" }} />
            <ZoomIndicator zoom={zoom} onReset={reset} />
          </div>

          <div className="surface p-3 mb-2">
            <div className="flex items-center gap-2 mb-2">
              <button onClick={handlePlay} className="px-3 py-1.5 bg-accent text-on-accent hover:bg-accent-hover rounded-lg text-sm min-w-[70px] font-medium">
                {playing ? "Pause" : "Play"}
              </button>
              <button onClick={() => seekToFrame(Math.max(0, currentFrame - 1))} disabled={currentFrame === 0}
                className="px-2 py-1.5 bg-white border rounded text-sm disabled:opacity-30">&larr;</button>
              <button onClick={() => seekToFrame(Math.min(frames.length - 1, currentFrame + 1))} disabled={currentFrame >= frames.length - 1}
                className="px-2 py-1.5 bg-white border rounded text-sm disabled:opacity-30">&rarr;</button>
              <span className="text-sm font-mono ml-auto" style={{ color: "var(--text-primary)" }}>
                {current ? `${current.timestamp_s.toFixed(2)}s` : ""}
              </span>
              <span className="text-sm font-mono" style={{ color: "var(--text-muted)" }}>{currentFrame + 1}/{frames.length}</span>
            </div>
            <input type="range" min={0} max={frames.length - 1} value={currentFrame}
              onInput={(e) => seekToFrame(Number((e.target as HTMLInputElement).value))}
              onChange={() => {}}
              className="w-full h-2 appearance-none bg-gray-300 rounded-full cursor-pointer
                [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-600 [&::-webkit-slider-thumb]:cursor-grab
                [&::-webkit-slider-thumb]:shadow-md [&::-webkit-slider-thumb]:active:cursor-grabbing
                [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:rounded-full
                [&::-moz-range-thumb]:bg-blue-600 [&::-moz-range-thumb]:border-none [&::-moz-range-thumb]:cursor-grab"
            />
            <TrackTimeline frames={frames} currentFrame={currentFrame} confThreshold={confThreshold}
              classFilter={classFilter} onSeek={seekToFrame} flaggedSet={flagged} />
          </div>

          {visibleDets.length > 0 && (
            <div className="surface p-3 mb-3">
              <div className="flex justify-between text-sm mb-2" style={{ color: "var(--text-muted)" }}>
                <span>Frame {current?.frame_index}</span>
                <span>{visibleDets.length} detections &middot; click to flag false positives</span>
              </div>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {visibleDets.map((d, i) => {
                  const key = `${currentFrame}-${d.track_id}`;
                  const isFlagged = flagged.has(key);
                  return (
                    <div key={i}
                      className="flex justify-between items-center text-sm rounded px-2 py-1.5 cursor-pointer transition-colors"
                      style={{
                        backgroundColor: isFlagged ? "var(--danger-soft)" : "var(--bg-surface)",
                        border: isFlagged ? "1px solid var(--danger)" : "1px solid transparent",
                      }}
                      onClick={() => toggleFlag(currentFrame, d)}
                    >
                      <span className="flex items-center gap-2">
                        <span className="inline-block w-3 h-3 rounded-sm shrink-0" style={{ backgroundColor: trackColor(d.track_id) }} />
                        <span style={{ textDecoration: isFlagged ? "line-through" : "none", color: isFlagged ? "var(--danger)" : "var(--text-primary)" }}>{d.class_name}</span>
                        {d.track_id != null && (
                          <span className="font-medium" style={{ color: trackColor(d.track_id) }}>#{d.track_id}</span>
                        )}
                      </span>
                      <span className="flex items-center gap-2">
                        <span className="font-mono" style={{ color: "var(--text-muted)" }}>{(d.confidence * 100).toFixed(1)}%</span>
                        {isFlagged ? (
                          <span className="text-xs font-medium" style={{ color: "var(--danger)" }}>Flagged</span>
                        ) : (
                          <span className="text-xs" style={{ color: "var(--text-muted)" }}>Flag</span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {flagged.size > 0 && (
            <div className="rounded-lg p-3 mb-3 flex items-center justify-between" style={{ backgroundColor: "var(--danger-soft)", border: "1px solid var(--danger)" }}>
              <span className="text-sm" style={{ color: "var(--danger)" }}>
                <strong>{flagged.size}</strong> detection{flagged.size !== 1 ? "s" : ""} flagged as false positive
              </span>
              <div className="flex items-center gap-2">
                <button onClick={() => setFlagged(new Set())}
                  className="px-3 py-1.5 text-sm rounded" style={{ color: "var(--danger)" }}>
                  Clear All
                </button>
                <button onClick={handleSubmitFeedback}
                  className="px-4 py-1.5 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700 font-medium">
                  Submit for Retraining
                </button>
              </div>
            </div>
          )}
          {feedbackMsg && (
            <div className="text-sm mb-3 flex items-center gap-2"
              style={{ color: feedbackMsg.startsWith("Error") ? "var(--danger)" : "var(--success)" }}>
              <span>{feedbackMsg}</span>
              {!feedbackMsg.startsWith("Error") && (
                <a href="/datasets" className="underline text-xs" style={{ color: "var(--accent)" }}>View in Datasets &rarr;</a>
              )}
            </div>
          )}

          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            {frames.length} frames &middot; {stats.uniqueTracks} tracked objects &middot; {stats.totalDets} total detections
            {zoom <= 1.01 && " \u00b7 Scroll to zoom, drag to pan"}
          </p>
        </div>
      )}
    </div>
  );
}
