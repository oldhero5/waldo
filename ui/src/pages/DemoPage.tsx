import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getServeStatus,
  predictImage,
  predictVideo,
  streamPredictFrames,
  type DetectionOut,
  type FrameResultOut,
} from "../api";
import { Link } from "react-router-dom";
import { AlertTriangle, ImageIcon, VideoIcon, ZoomIn, Move, Loader2 } from "lucide-react";

const COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"];

function trackColor(trackId: number | null): string {
  if (trackId == null) return COLORS[0];
  return COLORS[(trackId - 1) % COLORS.length];
}

function classColor(className: string, allClasses: string[]): string {
  const idx = allClasses.indexOf(className);
  return COLORS[Math.max(0, idx) % COLORS.length];
}

// ── Zoom + Pan hook ──────────────────────────────────────────

interface ZoomPan {
  zoom: number;
  panX: number;
  panY: number;
}

function useZoomPan(canvasRef: React.RefObject<HTMLCanvasElement | null>, redraw: () => void) {
  const [zp, setZp] = useState<ZoomPan>({ zoom: 1, panX: 0, panY: 0 });
  const zpRef = useRef(zp);
  zpRef.current = zp;
  const dragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const panStart = useRef({ x: 0, y: 0 });

  const reset = useCallback(() => setZp({ zoom: 1, panX: 0, panY: 0 }), []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      // Cursor position relative to canvas display area
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      // Scale from display coords to canvas coords
      const sx = canvas.width / rect.width;
      const sy = canvas.height / rect.height;
      const canvasX = cx * sx;
      const canvasY = cy * sy;

      setZp((prev) => {
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        const newZoom = Math.max(1, Math.min(prev.zoom * factor, 20));
        // Adjust pan so the point under the cursor stays fixed
        const newPanX = canvasX - (canvasX - prev.panX) * (newZoom / prev.zoom);
        const newPanY = canvasY - (canvasY - prev.panY) * (newZoom / prev.zoom);
        return { zoom: newZoom, panX: newPanX, panY: newPanY };
      });
    };

    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      dragging.current = true;
      dragStart.current = { x: e.clientX, y: e.clientY };
      panStart.current = { x: zpRef.current.panX, y: zpRef.current.panY };
      canvas.style.cursor = "grabbing";
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const rect = canvas.getBoundingClientRect();
      const sx = canvas.width / rect.width;
      const sy = canvas.height / rect.height;
      const dx = (e.clientX - dragStart.current.x) * sx;
      const dy = (e.clientY - dragStart.current.y) * sy;
      setZp((prev) => ({ ...prev, panX: panStart.current.x + dx, panY: panStart.current.y + dy }));
    };

    const onMouseUp = () => {
      dragging.current = false;
      canvas.style.cursor = zpRef.current.zoom > 1 ? "grab" : "default";
    };

    const onDblClick = () => setZp({ zoom: 1, panX: 0, panY: 0 });

    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    canvas.addEventListener("dblclick", onDblClick);

    return () => {
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      canvas.removeEventListener("dblclick", onDblClick);
    };
  }, [canvasRef]);

  // Trigger redraw when zoom/pan changes
  useEffect(() => { redraw(); }, [zp, redraw]);

  return { ...zp, reset };
}

// ── Drawing helpers ──────────────────────────────────────────

function drawDetections(
  ctx: CanvasRenderingContext2D,
  detections: DetectionOut[],
  confThreshold: number,
  canvasW: number,
  canvasH: number,
  srcW: number,
  srcH: number,
  classFilter?: Set<string>,
  zoom = 1,
) {
  const scaleX = canvasW / srcW;
  const scaleY = canvasH / srcH;
  let filtered = detections.filter((d) => d.confidence >= confThreshold);
  if (classFilter && classFilter.size > 0) {
    filtered = filtered.filter((d) => classFilter.has(d.class_name));
  }

  // Scale line width and font inversely with zoom so they stay readable
  const lw = Math.max(2, 3 / zoom);
  const fontSize = Math.max(10, Math.round(15 / zoom));

  for (const det of filtered) {
    const color = trackColor(det.track_id);
    const [x1, y1, x2, y2] = det.bbox.map((v, i) => v * (i % 2 === 0 ? scaleX : scaleY));

    // Draw filled mask first (behind the box)
    if (det.mask && det.mask.length > 0) {
      ctx.fillStyle = color + "55";
      ctx.beginPath();
      ctx.moveTo(det.mask[0][0] * scaleX, det.mask[0][1] * scaleY);
      for (const [x, y] of det.mask.slice(1)) ctx.lineTo(x * scaleX, y * scaleY);
      ctx.closePath();
      ctx.fill();
      // Stroke mask outline
      ctx.strokeStyle = color + "aa";
      ctx.lineWidth = lw * 0.75;
      ctx.stroke();
    }

    // Bounding box
    ctx.strokeStyle = color;
    ctx.lineWidth = lw;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

    // Label background + text
    const label = `${det.class_name} ${(det.confidence * 100).toFixed(0)}%${det.track_id != null ? ` #${det.track_id}` : ""}`;
    ctx.font = `bold ${fontSize}px system-ui, sans-serif`;
    const tw = ctx.measureText(label).width;
    const labelH = fontSize + 6;
    const labelY = y1 - labelH > 0 ? y1 - labelH : y1;
    // Semi-transparent background for label
    ctx.fillStyle = color + "dd";
    ctx.fillRect(x1, labelY, tw + 8, labelH);
    ctx.fillStyle = "#fff";
    ctx.fillText(label, x1 + 4, labelY + fontSize);
  }
}

function applyZoomPan(
  ctx: CanvasRenderingContext2D,
  zoom: number,
  panX: number,
  panY: number,
) {
  ctx.translate(panX, panY);
  ctx.scale(zoom, zoom);
}

function ZoomIndicator({ zoom, onReset }: { zoom: number; onReset: () => void }) {
  if (zoom <= 1.01) return null;
  return (
    <div className="absolute top-2 right-2 flex items-center gap-2 bg-black/60 text-white text-xs px-2 py-1 rounded">
      <span>{zoom.toFixed(1)}x</span>
      <button onClick={onReset} className="hover:text-gray-300">Reset</button>
    </div>
  );
}

// ── Image Demo ───────────────────────────────────────────────

function ImageDemo({ confThreshold, classFilter }: { confThreshold: number; classFilter: Set<string> }) {
  const [file, setFile] = useState<File | null>(null);
  const [detections, setDetections] = useState<DetectionOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !img.naturalWidth) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.save();
    applyZoomPan(ctx, zpRef.current.zoom, zpRef.current.panX, zpRef.current.panY);
    ctx.drawImage(img, 0, 0);
    drawDetections(ctx, detections, confThreshold, img.naturalWidth, img.naturalHeight, img.naturalWidth, img.naturalHeight, classFilter, zpRef.current.zoom);
    ctx.restore();
  }, [detections, confThreshold, classFilter]);

  const { zoom, panX, panY, reset } = useZoomPan(canvasRef, redraw);
  // Keep a ref so redraw can read current values without re-creating
  const zpRef = useRef({ zoom, panX, panY });
  zpRef.current = { zoom, panX, panY };

  useEffect(() => { redraw(); }, [redraw]);

  const handlePredict = useCallback(async () => {
    if (!file) return;
    setLoading(true);
    setError("");
    try {
      const result = await predictImage(file, confThreshold);
      setDetections(result.detections);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [file, confThreshold]);

  const previewUrl = file ? URL.createObjectURL(file) : null;

  const visibleCount = detections.filter(
    (d) => d.confidence >= confThreshold && classFilter.has(d.class_name)
  ).length;

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <label className="flex items-center gap-2 px-4 py-2 border-2 border-dashed border-gray-300 rounded-lg text-sm cursor-pointer hover:border-gray-400 hover:bg-gray-50">
          <ImageIcon size={16} className="text-gray-400" />
          Choose Image
          <input type="file" accept="image/*" className="hidden" onChange={(e) => { setFile(e.target.files?.[0] || null); setDetections([]); reset(); }} />
        </label>
        {file && (
          <button onClick={handlePredict} disabled={loading} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm disabled:opacity-40">
            {loading ? "Predicting..." : "Predict"}
          </button>
        )}
      </div>
      {error && <p className="text-red-600 text-sm mb-3">{error}</p>}
      {!previewUrl && !error && (
        <div className="border-2 border-dashed border-gray-200 rounded-xl p-12 text-center">
          <ImageIcon size={48} className="mx-auto mb-3 text-gray-300" />
          <p className="text-gray-400 text-sm mb-1">Upload an image to see predictions</p>
          <p className="text-gray-300 text-xs flex items-center justify-center gap-3">
            <span className="flex items-center gap-1"><ZoomIn size={12} /> Scroll to zoom</span>
            <span className="flex items-center gap-1"><Move size={12} /> Drag to pan</span>
          </p>
        </div>
      )}
      {previewUrl && (
        <div className="relative inline-block">
          <img ref={imgRef} src={previewUrl} alt="preview" className="hidden" onLoad={redraw} />
          <canvas
            ref={canvasRef}
            className="max-w-full rounded-lg border border-gray-200"
            style={{ cursor: zoom > 1 ? "grab" : "default" }}
          />
          <ZoomIndicator zoom={zoom} onReset={reset} />
          {loading && (
            <div className="absolute inset-0 bg-black/40 rounded-lg flex flex-col items-center justify-center">
              <Loader2 size={32} className="text-white animate-spin mb-2" />
              <p className="text-white text-sm font-medium">Running inference...</p>
            </div>
          )}
          {detections.length === 0 && !loading && (
            <img src={previewUrl} alt="preview" className="max-w-full rounded-lg border border-gray-200" />
          )}
        </div>
      )}
      {detections.length > 0 && (
        <p className="text-sm text-gray-500 mt-2">
          {visibleCount} detections shown &middot; Scroll to zoom, drag to pan, double-click to reset
        </p>
      )}
    </div>
  );
}

// ── Video Demo ───────────────────────────────────────────────

function TrackTimeline({ frames, currentFrame, confThreshold, classFilter, onSeek, flaggedSet }: {
  frames: FrameResultOut[];
  currentFrame: number;
  confThreshold: number;
  classFilter: Set<string>;
  onSeek: (idx: number) => void;
  flaggedSet: Set<string>;
}) {
  // Collect all unique tracks
  const trackIds = new Map<number, { color: string; className: string }>();
  for (const fr of frames) {
    for (const d of fr.detections) {
      if (d.track_id != null && d.confidence >= confThreshold && classFilter.has(d.class_name) && !trackIds.has(d.track_id)) {
        trackIds.set(d.track_id, { color: trackColor(d.track_id), className: d.class_name });
      }
    }
  }
  const sortedTracks = Array.from(trackIds.entries()).sort((a, b) => a[0] - b[0]);
  if (sortedTracks.length === 0) return null;

  const totalFrames = frames.length;
  const rowH = 14;
  const h = sortedTracks.length * rowH + 4;

  return (
    <div className="mt-1 mb-2">
      <p className="text-[10px] text-gray-400 mb-0.5">{sortedTracks.length} tracks</p>
      <div className="relative bg-gray-100 rounded overflow-hidden cursor-pointer" style={{ height: h }}
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const frac = (e.clientX - rect.left) / rect.width;
          onSeek(Math.round(frac * (totalFrames - 1)));
        }}
      >
        {/* Playhead */}
        <div
          className="absolute top-0 bottom-0 w-px bg-blue-600 z-10"
          style={{ left: `${(currentFrame / Math.max(1, totalFrames - 1)) * 100}%` }}
        />
        {/* Track lanes */}
        {sortedTracks.map(([tid, info], row) => {
          // Build segments: runs of consecutive frames where this track appears
          const segments: { start: number; end: number; hasFlagged: boolean }[] = [];
          let seg: { start: number; end: number; hasFlagged: boolean } | null = null;
          for (let i = 0; i < totalFrames; i++) {
            const det = frames[i].detections.find(
              (d) => d.track_id === tid && d.confidence >= confThreshold && classFilter.has(d.class_name)
            );
            if (det) {
              const key = `${i}-${tid}`;
              if (!seg) seg = { start: i, end: i, hasFlagged: flaggedSet.has(key) };
              else { seg.end = i; if (flaggedSet.has(key)) seg.hasFlagged = true; }
            } else {
              if (seg) { segments.push(seg); seg = null; }
            }
          }
          if (seg) segments.push(seg);

          return (
            <div key={tid} className="absolute left-0 right-0 flex items-center" style={{ top: row * rowH + 2, height: rowH }}>
              <span className="absolute -left-0 text-[8px] text-gray-400 w-4 text-right pr-0.5 select-none" style={{ color: info.color }}>
              </span>
              {segments.map((s, si) => (
                <div
                  key={si}
                  className="absolute rounded-sm"
                  style={{
                    left: `${(s.start / totalFrames) * 100}%`,
                    width: `${(Math.max(1, s.end - s.start + 1) / totalFrames) * 100}%`,
                    height: rowH - 4,
                    top: 2,
                    backgroundColor: s.hasFlagged ? "#fca5a5" : info.color,
                    opacity: s.hasFlagged ? 0.9 : 0.6,
                  }}
                />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function VideoDemo({ confThreshold, classFilter, classFilterArr, modelId }: {
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
  const [flagged, setFlagged] = useState<Set<string>>(new Set()); // "frameIdx-trackId" keys
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

  // Draw function that uses currentFrame index directly
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
      // Filter out flagged detections
      const visibleDets = fr.detections.filter((d) => {
        const key = `${frameIdx}-${d.track_id}`;
        return !flagged.has(key);
      });
      drawDetections(ctx, visibleDets, confThreshold, canvas.width, canvas.height, vw, vh, classFilter, zpRef.current.zoom);

      // Draw flagged detections with strikethrough
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
        // Draw X
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

  // Playback loop
  useEffect(() => {
    if (!playing) return;
    const tick = () => {
      const video = videoRef.current;
      if (video && frames.length > 0) {
        // Find closest frame to current video time
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

  // Seek when scrubbing (not playing)
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
    // Fallback: if seeked doesn't fire in 200ms, draw anyway
    setTimeout(() => {
      if (seekingRef.current) {
        seekingRef.current = false;
        drawAtFrame(idx);
      }
    }, 200);
  }, [frames, playing, drawAtFrame]);

  // Initial seek when frames first load
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

  // Flag / unflag a detection as false positive
  const toggleFlag = (frameIdx: number, det: DetectionOut) => {
    const key = `${frameIdx}-${det.track_id}`;
    setFlagged((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // Submit all flagged detections as feedback
  const handleSubmitFeedback = useCallback(async () => {
    if (flagged.size === 0) return;
    setFeedbackMsg("");
    const items: import("../api").FeedbackIn[] = [];
    for (const key of flagged) {
      const [fiStr, tidStr] = key.split("-");
      const fi = Number(fiStr);
      const tid = tidStr !== "null" ? Number(tidStr) : null;
      const fr = frames[fi];
      if (!fr) continue;
      const det = fr.detections.find((d) => d.track_id === tid);
      if (!det) continue;
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
      });
    }
    try {
      const { submitFeedbackBatch } = await import("../api");
      await submitFeedbackBatch(items);
      setFeedbackMsg(`${items.length} false positive${items.length !== 1 ? "s" : ""} saved. These become negative examples in your next training run — teaching the model what NOT to detect.`);
    } catch (e: any) {
      setFeedbackMsg(`Error: ${e.message}`);
    }
  }, [flagged, frames, modelId, file]);

  const uniqueTracks = new Set(
    frames.flatMap((f) => f.detections.filter((d) => d.track_id != null).map((d) => d.track_id))
  );
  const current = frames[currentFrame];
  const visibleDets = current?.detections.filter(
    (d) => d.confidence >= confThreshold && classFilter.has(d.class_name)
  ) || [];

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <label className="flex items-center gap-2 px-4 py-2 border-2 border-dashed border-gray-300 rounded-lg text-sm cursor-pointer hover:border-gray-400 hover:bg-gray-50">
          <VideoIcon size={16} className="text-gray-400" />
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

      {error && <p className="text-red-600 text-sm mb-3">{error}</p>}
      {loading && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-4">
          <div className="flex items-center gap-3 mb-2">
            <Loader2 size={18} className="text-gray-600 animate-spin shrink-0" />
            <span className="text-sm font-medium text-gray-700">
              {progress ? "Processing video frames..." : "Sending video to model..."}
            </span>
          </div>
          {progress ? (
            <div>
              <div className="flex items-center gap-3">
                <div className="flex-1 bg-gray-200 rounded-full h-2.5 overflow-hidden">
                  <div className="bg-blue-600 h-full rounded-full transition-all duration-300"
                    style={{ width: `${Math.round((progress.current / progress.total) * 100)}%` }} />
                </div>
                <span className="font-mono text-sm text-gray-600 w-32 text-right shrink-0">
                  {progress.current}/{progress.total} frames
                </span>
              </div>
              <p className="text-xs text-gray-400 mt-1">{Math.round((progress.current / progress.total) * 100)}% complete</p>
            </div>
          ) : (
            <div className="w-full bg-gray-200 rounded-full h-2.5 overflow-hidden">
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
            <canvas ref={canvasRef} className="rounded-lg border border-gray-200 mb-2 bg-black"
              style={{ maxWidth: "100%", cursor: zoom > 1 ? "grab" : "default" }} />
            <ZoomIndicator zoom={zoom} onReset={reset} />
          </div>

          {/* Transport controls */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 mb-2">
            <div className="flex items-center gap-2 mb-2">
              <button onClick={handlePlay} className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm min-w-[70px] font-medium">
                {playing ? "Pause" : "Play"}
              </button>
              <button onClick={() => seekToFrame(Math.max(0, currentFrame - 1))} disabled={currentFrame === 0}
                className="px-2 py-1.5 bg-white border rounded text-sm disabled:opacity-30">&larr;</button>
              <button onClick={() => seekToFrame(Math.min(frames.length - 1, currentFrame + 1))} disabled={currentFrame >= frames.length - 1}
                className="px-2 py-1.5 bg-white border rounded text-sm disabled:opacity-30">&rarr;</button>
              <span className="text-sm text-gray-700 font-mono ml-auto">
                {current ? `${current.timestamp_s.toFixed(2)}s` : ""}
              </span>
              <span className="text-sm text-gray-400 font-mono">{currentFrame + 1}/{frames.length}</span>
            </div>
            {/* Scrubber */}
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
            {/* Track timeline */}
            <TrackTimeline frames={frames} currentFrame={currentFrame} confThreshold={confThreshold}
              classFilter={classFilter} onSeek={seekToFrame} flaggedSet={flagged} />
          </div>

          {/* Detection list with flag buttons */}
          {visibleDets.length > 0 && (
            <div className="bg-gray-50 rounded-lg p-3 mb-3">
              <div className="flex justify-between text-sm text-gray-500 mb-2">
                <span>Frame {current.frame_index}</span>
                <span>{visibleDets.length} detections &middot; click to flag false positives</span>
              </div>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {visibleDets.map((d, i) => {
                  const key = `${currentFrame}-${d.track_id}`;
                  const isFlagged = flagged.has(key);
                  return (
                    <div key={i}
                      className={`flex justify-between items-center text-sm rounded px-2 py-1.5 cursor-pointer transition-colors ${
                        isFlagged ? "bg-red-50 border border-red-200" : "bg-white hover:bg-red-50/50"
                      }`}
                      onClick={() => toggleFlag(currentFrame, d)}
                    >
                      <span className="flex items-center gap-2">
                        <span className="inline-block w-3 h-3 rounded-sm shrink-0" style={{ backgroundColor: trackColor(d.track_id) }} />
                        <span className={isFlagged ? "line-through text-red-400" : ""}>{d.class_name}</span>
                        {d.track_id != null && (
                          <span className="font-medium" style={{ color: trackColor(d.track_id) }}>#{d.track_id}</span>
                        )}
                      </span>
                      <span className="flex items-center gap-2">
                        <span className="font-mono text-gray-500">{(d.confidence * 100).toFixed(1)}%</span>
                        {isFlagged ? (
                          <span className="text-xs text-red-500 font-medium">Flagged</span>
                        ) : (
                          <span className="text-xs text-gray-300">Flag</span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Submit feedback bar */}
          {flagged.size > 0 && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-3 flex items-center justify-between">
              <span className="text-sm text-red-700">
                <strong>{flagged.size}</strong> detection{flagged.size !== 1 ? "s" : ""} flagged as false positive
              </span>
              <div className="flex items-center gap-2">
                <button onClick={() => setFlagged(new Set())}
                  className="px-3 py-1.5 text-sm text-red-600 hover:bg-red-100 rounded">
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
            <div className={`text-sm mb-3 flex items-center gap-2 ${feedbackMsg.startsWith("Error") ? "text-red-600" : "text-green-600"}`}>
              <span>{feedbackMsg}</span>
              {!feedbackMsg.startsWith("Error") && (
                <a href="/datasets" className="underline text-blue-500 hover:text-blue-400 text-xs">View in Datasets &rarr;</a>
              )}
            </div>
          )}

          <p className="text-sm text-gray-500">
            {frames.length} frames &middot; {uniqueTracks.size} tracked objects &middot;{" "}
            {frames.reduce((s, f) => s + f.detections.filter((d) => d.confidence >= confThreshold && classFilter.has(d.class_name)).length, 0)} total detections
            {zoom <= 1.01 && " \u00b7 Scroll to zoom, drag to pan"}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────

export default function DemoPage() {
  const [mode, setMode] = useState<"image" | "video">("image");
  const [confThreshold, setConfThreshold] = useState(0.25);
  const [checkedClasses, setCheckedClasses] = useState<Set<string>>(new Set());

  const { data: status } = useQuery({
    queryKey: ["serve-status"],
    queryFn: getServeStatus,
  });

  // Initialize checked classes when status loads
  useEffect(() => {
    if (status?.class_names && checkedClasses.size === 0) {
      setCheckedClasses(new Set(status.class_names));
    }
  }, [status?.class_names]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleClass = (cls: string) => {
    setCheckedClasses((prev) => {
      const next = new Set(prev);
      if (next.has(cls)) next.delete(cls);
      else next.add(cls);
      return next;
    });
  };

  const allClasses = status?.class_names || [];
  const classFilterArr = allClasses.length > 0
    ? allClasses.filter((c) => checkedClasses.has(c))
    : [];

  return (
    <div className="min-h-screen">

      <div className="max-w-4xl mx-auto mt-8 px-4">
        <p className="eyebrow" style={{ marginBottom: 4 }}>Inference playground</p>
        <h1 className="text-2xl font-bold text-gray-900 mb-2">Demo</h1>
        {status && !status.loaded && (
          <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 text-sm text-amber-800">
            <AlertTriangle size={16} className="shrink-0" />
            <span>
              No model loaded.{" "}
              <Link to="/deploy" className="font-medium underline hover:text-amber-900">
                Go to Deploy
              </Link>{" "}
              and activate a model first.
            </span>
          </div>
        )}
        {status?.loaded && (
          <p className="text-gray-500 text-sm mb-4">Model: {status.model_name} ({status.model_variant}) on {status.device}</p>
        )}

        <div className="flex items-center gap-4 mb-6 flex-wrap">
          <div className="flex bg-gray-100 rounded-lg p-1">
            <button onClick={() => setMode("image")} className={`px-4 py-1.5 text-sm rounded-md ${mode === "image" ? "bg-white shadow-sm font-medium" : "text-gray-500"}`}>Image</button>
            <button onClick={() => setMode("video")} className={`px-4 py-1.5 text-sm rounded-md ${mode === "video" ? "bg-white shadow-sm font-medium" : "text-gray-500"}`}>Video</button>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-sm text-gray-600">Confidence:</label>
            <input type="range" min={0} max={1} step={0.05} value={confThreshold} onChange={(e) => setConfThreshold(Number(e.target.value))} className="w-32" />
            <span className="text-sm font-mono w-12">{(confThreshold * 100).toFixed(0)}%</span>
          </div>
        </div>

        {/* Class filter checkboxes */}
        {allClasses.length > 0 && (
          <div className="mb-6">
            <p className="text-sm font-medium text-gray-700 mb-2">Classes</p>
            <div className="flex flex-wrap gap-3">
              {allClasses.map((cls) => (
                <label key={cls} className="flex items-center gap-1.5 text-sm cursor-pointer" title={cls}>
                  <input
                    type="checkbox"
                    checked={checkedClasses.has(cls)}
                    onChange={() => toggleClass(cls)}
                    className="rounded"
                  />
                  <span className="inline-block w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: classColor(cls, allClasses) }} />
                  <span className="truncate max-w-[120px]">{cls}</span>
                </label>
              ))}
              <button
                onClick={() => setCheckedClasses(new Set(allClasses))}
                className="text-xs text-blue-600 hover:underline"
              >
                All
              </button>
              <button
                onClick={() => setCheckedClasses(new Set())}
                className="text-xs text-blue-600 hover:underline"
              >
                None
              </button>
            </div>
          </div>
        )}

        {mode === "image"
          ? <ImageDemo confThreshold={confThreshold} classFilter={checkedClasses} />
          : <VideoDemo confThreshold={confThreshold} classFilter={checkedClasses} classFilterArr={classFilterArr}
              modelId={status?.model_id || null} />
        }
      </div>
    </div>
  );
}
