import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, VideoIcon } from "lucide-react";
import {
  predictVideo,
  streamPredictFrames,
  type DetectionOut,
  type FrameResultOut,
} from "../../api";
import { applyZoomPan, drawDetections, trackColor, useZoomPan, ZoomIndicator } from "./shared";
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
  const [flagged, setFlagged] = useState<Set<string>>(new Set());
  const [feedbackMsg, setFeedbackMsg] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const wsCleanupRef = useRef<(() => void) | null>(null);
  const seekingRef = useRef(false);

  useEffect(() => {
    if (!file) { setVideoUrl(""); setVideoReady(false); return; }
    const url = URL.createObjectURL(file);
    setVideoUrl(url);
    setVideoReady(false);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    return () => { wsCleanupRef.current?.(); };
  }, []);

  const handlePredict = useCallback(async () => {
    if (!file) return;
    wsCleanupRef.current?.();
    setLoading(true);
    setError("");
    setFrames([]);
    setCurrentFrame(0);
    setPlaying(false);
    setProgress(null);
    setFlagged(new Set());
    setFeedbackMsg("");
    try {
      const result = await predictVideo(file, confThreshold, classFilterArr.length > 0 ? classFilterArr : undefined);
      if ("frames" in result) {
        setFrames(result.frames);
      } else {
        setProgress({ current: 0, total: result.frame_count });
        const streamedFrames: FrameResultOut[] = [];
        wsCleanupRef.current = streamPredictFrames(
          result.session_id,
          (frame) => {
            streamedFrames.push(frame);
            setProgress({ current: streamedFrames.length, total: result.frame_count });
          },
          () => { setFrames([...streamedFrames]); setLoading(false); setProgress(null); },
          (err) => {
            if (streamedFrames.length > 0) setFrames([...streamedFrames]);
            setError(err); setLoading(false); setProgress(null);
          },
        );
        return;
      }
    } catch (e: any) {
      setError(e.message);
    }
    setLoading(false);
  }, [file, confThreshold, classFilterArr]);

  const drawAtFrame = useCallback((frameIdx: number) => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !frames.length || !video.videoWidth) return;

    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const maxW = 960;
    const scale = Math.min(maxW / vw, 1);
    canvas.width = vw * scale;
    canvas.height = vh * scale;

    const ctx = canvas.getContext("2d")!;
    ctx.save();
    applyZoomPan(ctx, zpRef.current.zoom, zpRef.current.panX, zpRef.current.panY);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    const fr = frames[frameIdx];
    if (fr) {
      const visibleDets = fr.detections.filter((d) => {
        const key = `${frameIdx}-${d.track_id}`;
        return !flagged.has(key);
      });
      drawDetections(ctx, visibleDets, confThreshold, canvas.width, canvas.height, vw, vh, classFilter, zpRef.current.zoom);

      const flaggedDets = fr.detections.filter((d) => {
        const key = `${frameIdx}-${d.track_id}`;
        return flagged.has(key) && d.confidence >= confThreshold && classFilter.has(d.class_name);
      });
      for (const det of flaggedDets) {
        const scaleX = canvas.width / vw;
        const scaleY = canvas.height / vh;
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

  const drawFrame = useCallback(() => { drawAtFrame(currentFrame); }, [drawAtFrame, currentFrame]);

  const { zoom, panX, panY, reset } = useZoomPan(canvasRef, drawFrame);
  const zpRef = useRef({ zoom, panX, panY });
  zpRef.current = { zoom, panX, panY };

  useEffect(() => {
    if (!playing) return;
    const tick = () => {
      const video = videoRef.current;
      if (video && frames.length > 0) {
        let closest = 0;
        let minDiff = Infinity;
        for (let i = 0; i < frames.length; i++) {
          const diff = Math.abs(frames[i].timestamp_s - video.currentTime);
          if (diff < minDiff) { minDiff = diff; closest = i; }
        }
        setCurrentFrame(closest);
        drawAtFrame(closest);
      }
      animRef.current = requestAnimationFrame(tick);
    };
    animRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animRef.current);
  }, [playing, drawAtFrame, frames]);

  const seekToFrame = useCallback((idx: number) => {
    if (playing) { videoRef.current?.pause(); setPlaying(false); }
    setCurrentFrame(idx);
    const video = videoRef.current;
    if (!video || !frames[idx]) return;
    seekingRef.current = true;
    video.currentTime = frames[idx].timestamp_s;
    const onSeeked = () => {
      seekingRef.current = false;
      drawAtFrame(idx);
    };
    video.addEventListener("seeked", onSeeked, { once: true });
    setTimeout(() => {
      if (seekingRef.current) {
        seekingRef.current = false;
        drawAtFrame(idx);
      }
    }, 200);
  }, [frames, playing, drawAtFrame]);

  useEffect(() => {
    if (!frames.length || !videoReady) return;
    seekToFrame(0);
  }, [frames.length > 0, videoReady]); // eslint-disable-line react-hooks/exhaustive-deps

  const handlePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (playing) { video.pause(); setPlaying(false); }
    else { video.play(); setPlaying(true); }
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

  const captureFrameB64 = useCallback((timestamp: number): Promise<string | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return Promise.resolve(null);
    return new Promise((resolve) => {
      const prev = video.currentTime;
      const onSeeked = () => {
        video.removeEventListener("seeked", onSeeked);
        const tmp = document.createElement("canvas");
        tmp.width = video.videoWidth;
        tmp.height = video.videoHeight;
        const ctx = tmp.getContext("2d");
        if (!ctx) { resolve(null); return; }
        ctx.drawImage(video, 0, 0);
        const b64 = tmp.toDataURL("image/jpeg", 0.85).split(",")[1];
        video.currentTime = prev;
        resolve(b64);
      };
      video.addEventListener("seeked", onSeeked);
      video.currentTime = timestamp;
    });
  }, []);

  const handleSubmitFeedback = useCallback(async () => {
    if (flagged.size === 0) return;
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

    for (const [, { fr, dets }] of byFrame) {
      const b64 = await captureFrameB64(fr.timestamp_s);
      for (const det of dets) {
        items.push({
          model_id: modelId || undefined,
          class_name: det.class_name,
          bbox: det.bbox,
          polygon: det.mask,
          confidence: det.confidence,
          track_id: det.track_id,
          frame_index: fr.frame_index,
          timestamp_s: fr.timestamp_s,
          feedback_type: "false_positive",
          source_filename: file?.name,
          frame_image_b64: b64 || undefined,
        });
      }
    }

    try {
      const { submitFeedbackBatch } = await import("../../api");
      setFeedbackMsg("Submitting...");
      await submitFeedbackBatch(items);
      setFeedbackMsg(`${items.length} false positive${items.length !== 1 ? "s" : ""} saved. These become negative examples in your next training run.`);
    } catch (e: any) {
      setFeedbackMsg(`Error: ${e.message}`);
    }
  }, [flagged, frames, modelId, file, captureFrameB64]);

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

  const current = frames[currentFrame];
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
            setFile(e.target.files?.[0] || null);
            setFrames([]); setCurrentFrame(0); setFlagged(new Set()); setFeedbackMsg(""); reset();
          }} />
        </label>
        {file && (
          <button onClick={handlePredict} disabled={loading} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm disabled:opacity-40">
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
              {progress ? "Processing video frames..." : "Sending video to model..."}
            </span>
          </div>
          {progress ? (
            <div>
              <div className="flex items-center gap-3">
                <div className="flex-1 rounded-full h-2.5 overflow-hidden" style={{ backgroundColor: "var(--bg-inset)" }}>
                  <div className="bg-blue-600 h-full rounded-full transition-all duration-300"
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
              <div className="bg-blue-600 h-full rounded-full animate-pulse w-1/3" />
            </div>
          )}
        </div>
      )}

      {videoUrl && (
        <video ref={videoRef} src={videoUrl} className="hidden" muted playsInline preload="auto"
          onLoadedData={() => setVideoReady(true)} onEnded={() => setPlaying(false)} />
      )}

      {frames.length > 0 && (
        <div>
          <div className="relative inline-block">
            <canvas ref={canvasRef} className="rounded-lg mb-2 bg-black"
              style={{ maxWidth: "100%", cursor: zoom > 1 ? "grab" : "default", border: "1px solid var(--border-subtle)" }} />
            <ZoomIndicator zoom={zoom} onReset={reset} />
          </div>

          <div className="surface p-3 mb-2">
            <div className="flex items-center gap-2 mb-2">
              <button onClick={handlePlay} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm min-w-[70px] font-medium">
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
                <span>Frame {current.frame_index}</span>
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
