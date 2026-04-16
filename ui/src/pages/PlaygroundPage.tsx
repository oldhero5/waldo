/**
 * SAM 3.1 Prompt Playground.
 *
 * Lets the user iterate on prompts + threshold against the first few seconds
 * of a video before committing to a full labeling job. Runs as a contiguous
 * window (not strided samples) so SimpleTracker can assign consistent
 * track_ids across frames — essential for verifying tracking will actually
 * dedupe objects during real labeling.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Wand2,
  Play,
  Pause,
  Plus,
  X,
  Loader2,
  Rocket,
  Info,
  Maximize2,
  Minimize2,
  ZoomIn,
  Crosshair,
} from "lucide-react";
import {
  listProjects,
  listProjectVideos,
  previewPrompts,
  startLabeling,
  type PreviewResponse,
  type VideoOut,
} from "../api";

type PromptDraft = { id: number; value: string };
type PreviewFrame = PreviewResponse["frames"][number];
type PreviewDetection = PreviewFrame["detections"][number];

function hashHue(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 360;
}

function colorForTrack(trackId: number | null, fallbackLabel: string): string {
  if (trackId != null && trackId >= 0) {
    return `hsl(${(trackId * 57) % 360} 80% 60%)`;
  }
  return `hsl(${hashHue(fallbackLabel)} 80% 60%)`;
}

function formatTime(s: number): string {
  if (!isFinite(s) || s < 0) return "0:00.0";
  const mins = Math.floor(s / 60);
  const secs = s - mins * 60;
  return `${mins}:${secs.toFixed(1).padStart(4, "0")}`;
}

// ── Detection SVG overlay — renders polygons (or bbox fallback) on top
// of whatever's underneath (video element or base64 JPEG).
function DetectionOverlay({
  frame,
  selectedTrackId,
}: {
  frame: PreviewFrame;
  selectedTrackId: number | null;
}) {
  return (
    <svg
      viewBox={`0 0 ${frame.width} ${frame.height}`}
      preserveAspectRatio="none"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
      }}
    >
      {frame.detections.map((d, i) => {
        const selected = selectedTrackId != null && d.track_id === selectedTrackId;
        const color = colorForTrack(d.track_id, d.label);
        const strokeW = Math.max(2, frame.width / 400) * (selected ? 1.6 : 1);
        const tag = d.track_id != null ? `#${d.track_id} ${d.label}` : d.label;

        let pointsStr: string | null = null;
        let labelX = 0;
        let labelY = 0;
        if (d.polygon && d.polygon.length >= 6) {
          const coords: string[] = [];
          let minX = Infinity;
          let minY = Infinity;
          for (let j = 0; j < d.polygon.length; j += 2) {
            const px = d.polygon[j] * frame.width;
            const py = d.polygon[j + 1] * frame.height;
            coords.push(`${px},${py}`);
            if (px < minX) minX = px;
            if (py < minY) minY = py;
          }
          pointsStr = coords.join(" ");
          labelX = minX;
          labelY = minY;
        } else if (d.bbox && d.bbox.length >= 4) {
          labelX = d.bbox[0];
          labelY = d.bbox[1];
        } else {
          return null;
        }

        return (
          <g key={i}>
            {pointsStr ? (
              <polygon
                points={pointsStr}
                fill={color}
                fillOpacity={selected ? 0.45 : 0.25}
                stroke={color}
                strokeWidth={strokeW}
                strokeLinejoin="round"
              />
            ) : d.bbox && d.bbox.length >= 4 ? (
              <rect
                x={d.bbox[0]}
                y={d.bbox[1]}
                width={Math.max(0, d.bbox[2] - d.bbox[0])}
                height={Math.max(0, d.bbox[3] - d.bbox[1])}
                fill="none"
                stroke={color}
                strokeWidth={strokeW}
              />
            ) : null}
            <rect
              x={labelX}
              y={Math.max(0, labelY - 22)}
              width={Math.min(frame.width - labelX, tag.length * 9 + 50)}
              height={20}
              fill={color}
              opacity={0.9}
            />
            <text
              x={labelX + 6}
              y={Math.max(14, labelY - 7)}
              fontSize={14}
              fontFamily="SF Mono, monospace"
              fill="#0f0e0c"
              fontWeight={600}
            >
              {tag} · {(d.score * 100).toFixed(0)}%
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ── Unique-track aggregation used by the detection list sidebar.
type TrackEntry = {
  trackId: number;
  label: string;
  bestScore: number;
  bestFrame: PreviewFrame;
  bestDetection: PreviewDetection;
  frameCount: number;
};

function extractTracks(frames: PreviewFrame[]): TrackEntry[] {
  const map = new Map<number, TrackEntry>();
  for (const f of frames) {
    for (const d of f.detections) {
      if (d.track_id == null) continue;
      const existing = map.get(d.track_id);
      if (!existing) {
        map.set(d.track_id, {
          trackId: d.track_id,
          label: d.label,
          bestScore: d.score,
          bestFrame: f,
          bestDetection: d,
          frameCount: 1,
        });
      } else {
        existing.frameCount += 1;
        if (d.score > existing.bestScore) {
          existing.bestScore = d.score;
          existing.bestFrame = f;
          existing.bestDetection = d;
        }
      }
    }
  }
  return Array.from(map.values()).sort((a, b) => b.bestScore - a.bestScore);
}

// ── Video-based preview player. Replaces the old static frame grid.
// Shows the actual video playing with detection polygons overlaid at the
// nearest sampled frame's timestamp, plus a timeline, scrubber, fullscreen
// toggle, and zoom-to-detection.
function PreviewPlayer({
  result,
  videoUrl,
  startSec,
  durationSec,
}: {
  result: PreviewResponse;
  videoUrl: string;
  startSec: number;
  durationSec: number;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [currentTime, setCurrentTime] = useState(startSec);
  const [playing, setPlaying] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [selectedTrackId, setSelectedTrackId] = useState<number | null>(null);
  // Zoom state: `txPct` / `tyPct` are CSS translate percentages of the
  // container's own width/height. With `transform-origin: center center`,
  // the math to center a point P on the container center C simplifies to
  // T = C - P (scale-independent). See zoomToDetection() for derivation.
  const [zoom, setZoom] = useState<{
    scale: number;
    txPct: number;
    tyPct: number;
  } | null>(null);

  const tracks = useMemo(() => extractTracks(result.frames), [result.frames]);
  const windowEnd = startSec + durationSec;

  // Nearest sampled frame for the current video time — drives overlay.
  const activeFrame = useMemo(() => {
    if (!result.frames.length) return null;
    let best = result.frames[0];
    let bestDist = Math.abs(best.timestamp_s - currentTime);
    for (const f of result.frames) {
      const d = Math.abs(f.timestamp_s - currentTime);
      if (d < bestDist) {
        best = f;
        bestDist = d;
      }
    }
    return best;
  }, [result.frames, currentTime]);

  // Seek to window start when the source changes.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onLoaded = () => {
      v.currentTime = startSec;
      setCurrentTime(startSec);
    };
    v.addEventListener("loadedmetadata", onLoaded);
    return () => v.removeEventListener("loadedmetadata", onLoaded);
  }, [videoUrl, startSec]);

  // Clamp playback to [startSec, windowEnd] — loop back to start.
  const onTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.currentTime >= windowEnd - 0.01) {
      v.currentTime = startSec;
    }
    setCurrentTime(v.currentTime);
  };

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      if (v.currentTime < startSec || v.currentTime >= windowEnd - 0.01) {
        v.currentTime = startSec;
      }
      v.play();
      setPlaying(true);
    } else {
      v.pause();
      setPlaying(false);
    }
  };

  const seekTo = (t: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = Math.max(startSec, Math.min(windowEnd, t));
    setCurrentTime(v.currentTime);
  };

  // Zoom to a detection: pause, seek to its frame, transform the stage so
  // the bbox is centered and fills ~`targetFraction` of the viewport.
  //
  // Math: with `transform-origin: center center`, applying
  //   `transform: scale(S) translate(Tx%, Ty%)`
  // sends a point P (in container-local coords) to
  //   P' = S*P + (1-S)*C + S*T
  // where C is the container center and T is the translate in pre-scale
  // CSS pixels (percentages here are resolved against the element's own
  // box, which equals the container since the stage is inset:0).
  //
  // Setting P' = C and solving gives T = C - P — independent of S. So the
  // recipe is: translate by (container_center - bbox_center_in_container),
  // then scale around center. Both are expressed as fractions of the
  // container (× 100 → percentages) so we don't need live viewport pixels.
  const zoomToDetection = useCallback(
    (track: TrackEntry) => {
      const v = videoRef.current;
      if (!v) return;
      v.pause();
      setPlaying(false);
      v.currentTime = track.bestFrame.timestamp_s;
      setCurrentTime(track.bestFrame.timestamp_s);
      setSelectedTrackId(track.trackId);

      const d = track.bestDetection;

      // Prefer the polygon's bounds (tighter than the bbox when the mask
      // doesn't fill its axis-aligned box). Fall back to the bbox.
      let x1: number, y1: number, x2: number, y2: number;
      if (d.polygon && d.polygon.length >= 6) {
        x1 = Infinity;
        y1 = Infinity;
        x2 = -Infinity;
        y2 = -Infinity;
        for (let i = 0; i < d.polygon.length; i += 2) {
          const px = d.polygon[i] * track.bestFrame.width;
          const py = d.polygon[i + 1] * track.bestFrame.height;
          if (px < x1) x1 = px;
          if (py < y1) y1 = py;
          if (px > x2) x2 = px;
          if (py > y2) y2 = py;
        }
      } else if (d.bbox && d.bbox.length >= 4) {
        [x1, y1, x2, y2] = d.bbox;
      } else {
        return;
      }

      const frameW = track.bestFrame.width;
      const frameH = track.bestFrame.height;

      // Normalized bbox in [0,1] within the video's intrinsic coords —
      // also equals the bbox in container-local fractional coords because
      // the container's aspect ratio matches the video (no letterboxing).
      const cxNorm = ((x1 + x2) / 2) / frameW;
      const cyNorm = ((y1 + y2) / 2) / frameH;
      const bboxWNorm = Math.max(1e-4, (x2 - x1) / frameW);
      const bboxHNorm = Math.max(1e-4, (y2 - y1) / frameH);

      const targetFraction = 0.55; // bbox fills ~55% of the shorter axis
      const scale = Math.min(
        targetFraction / bboxWNorm,
        targetFraction / bboxHNorm,
        8
      );

      // T = C - P, where C = (0.5, 0.5) and P = (cxNorm, cyNorm).
      // Convert to CSS translate percentages of the element's own box.
      const txPct = (0.5 - cxNorm) * 100;
      const tyPct = (0.5 - cyNorm) * 100;

      setZoom({ scale, txPct, tyPct });
    },
    []
  );

  const resetZoom = () => {
    setZoom(null);
    setSelectedTrackId(null);
  };

  const toggleFullscreen = () => {
    const el = containerRef.current;
    if (!el) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      el.requestFullscreen().catch(() => {});
    }
  };

  useEffect(() => {
    const onChange = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const progressPct = ((currentTime - startSec) / Math.max(0.0001, durationSec)) * 100;

  return (
    <div className="space-y-3">
      <div
        ref={containerRef}
        className="surface overflow-hidden"
        style={{
          borderRadius: "var(--radius-lg)",
          background: "#000",
          position: "relative",
        }}
      >
        {/* Stage: video + overlay, transformed together for zoom */}
        <div
          style={{
            position: "relative",
            width: "100%",
            aspectRatio: activeFrame
              ? `${activeFrame.width} / ${activeFrame.height}`
              : "16 / 9",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              transformOrigin: "center center",
              // Order matters: `scale(s) translate(Tx%, Ty%)` reads as
              // matrix(scale) · matrix(translate) · P. With origin-center
              // the translate percentages land in pre-scale container
              // fractions, which is exactly what zoomToDetection emits.
              transform: zoom
                ? `scale(${zoom.scale}) translate(${zoom.txPct}%, ${zoom.tyPct}%)`
                : "none",
              transition: "transform 320ms cubic-bezier(0.2, 0.8, 0.2, 1)",
              willChange: "transform",
            }}
          >
            <video
              ref={videoRef}
              src={videoUrl}
              onTimeUpdate={onTimeUpdate}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              playsInline
              muted
              preload="auto"
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                objectFit: "contain",
                display: "block",
              }}
            />
            {activeFrame && (
              <DetectionOverlay
                frame={activeFrame}
                selectedTrackId={selectedTrackId}
              />
            )}
          </div>

          {/* Top-right fullscreen toggle */}
          <button
            onClick={toggleFullscreen}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg"
            style={{
              position: "absolute",
              top: 10,
              right: 10,
              backgroundColor: "rgba(0,0,0,0.6)",
              color: "#fff",
              border: "1px solid rgba(255,255,255,0.2)",
              fontSize: 11,
              backdropFilter: "blur(8px)",
            }}
            title="Fullscreen"
          >
            {fullscreen ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
            {fullscreen ? "Exit" : "Full"}
          </button>

          {zoom && (
            <button
              onClick={resetZoom}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg"
              style={{
                position: "absolute",
                top: 10,
                left: 10,
                backgroundColor: "var(--accent)",
                color: "var(--bg-page)",
                fontSize: 11,
                fontWeight: 600,
              }}
              title="Reset zoom"
            >
              <Crosshair size={12} />
              Reset zoom
            </button>
          )}
        </div>

        {/* Bottom control bar */}
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            padding: "14px 14px 10px",
            background:
              "linear-gradient(to top, rgba(0,0,0,0.85), rgba(0,0,0,0))",
            pointerEvents: "none",
          }}
        >
          <div
            className="flex items-center gap-3 mb-2"
            style={{ pointerEvents: "auto" }}
          >
            <button
              onClick={togglePlay}
              className="flex items-center justify-center rounded-full"
              style={{
                width: 32,
                height: 32,
                backgroundColor: "var(--accent)",
                color: "var(--bg-page)",
              }}
            >
              {playing ? <Pause size={14} /> : <Play size={14} />}
            </button>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "#fff",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {formatTime(currentTime - startSec)} / {formatTime(durationSec)}
            </span>
            <span
              style={{
                fontSize: 10,
                fontFamily: "var(--font-mono)",
                color: "rgba(255,255,255,0.55)",
              }}
            >
              window {formatTime(startSec)}–{formatTime(windowEnd)}
            </span>
          </div>

          {/* Scrubber + detection tick track */}
          <div
            className="relative"
            style={{ pointerEvents: "auto", height: 22 }}
          >
            {/* Track background */}
            <div
              style={{
                position: "absolute",
                left: 0,
                right: 0,
                top: 9,
                height: 4,
                borderRadius: 2,
                backgroundColor: "rgba(255,255,255,0.18)",
              }}
            />
            {/* Progress fill */}
            <div
              style={{
                position: "absolute",
                left: 0,
                top: 9,
                width: `${Math.max(0, Math.min(100, progressPct))}%`,
                height: 4,
                borderRadius: 2,
                backgroundColor: "var(--accent)",
              }}
            />
            {/* Detection ticks — one per sampled frame that had detections */}
            {result.frames.map((f) => {
              if (f.detections.length === 0) return null;
              const pct = ((f.timestamp_s - startSec) / durationSec) * 100;
              if (pct < 0 || pct > 100) return null;
              return (
                <div
                  key={f.frame_idx}
                  style={{
                    position: "absolute",
                    left: `${pct}%`,
                    top: 5,
                    width: 2,
                    height: 12,
                    backgroundColor: "var(--accent)",
                    opacity: 0.75,
                    transform: "translateX(-1px)",
                  }}
                  title={`t=${f.timestamp_s.toFixed(2)}s · ${f.detections.length} detection${f.detections.length === 1 ? "" : "s"}`}
                />
              );
            })}
            <input
              type="range"
              min={startSec}
              max={windowEnd}
              step={0.01}
              value={currentTime}
              onChange={(e) => seekTo(Number(e.target.value))}
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                opacity: 0,
                cursor: "pointer",
              }}
            />
            {/* Scrubber thumb */}
            <div
              style={{
                position: "absolute",
                left: `${Math.max(0, Math.min(100, progressPct))}%`,
                top: 5,
                width: 12,
                height: 12,
                marginLeft: -6,
                borderRadius: 6,
                backgroundColor: "#fff",
                border: "2px solid var(--accent)",
                pointerEvents: "none",
              }}
            />
          </div>
        </div>
      </div>

      {/* Detection list — click a row to zoom */}
      {tracks.length > 0 && (
        <div
          className="surface p-4"
          style={{ borderRadius: "var(--radius-lg)" }}
        >
          <div className="flex items-center justify-between mb-2">
            <p className="eyebrow">
              Detected objects ({tracks.length} unique)
            </p>
            {zoom && (
              <button
                onClick={resetZoom}
                className="flex items-center gap-1 text-xs"
                style={{ color: "var(--accent)" }}
              >
                <Crosshair size={11} />
                Clear zoom
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-72 overflow-y-auto">
            {tracks.map((t) => {
              const color = colorForTrack(t.trackId, t.label);
              const selected = t.trackId === selectedTrackId;
              return (
                <button
                  key={t.trackId}
                  onClick={() => zoomToDetection(t)}
                  className="flex items-center gap-3 px-3 py-2 rounded-lg text-left"
                  style={{
                    backgroundColor: selected
                      ? "var(--accent-soft)"
                      : "var(--bg-inset)",
                    border: selected
                      ? "1px solid var(--accent)"
                      : "1px solid transparent",
                  }}
                >
                  <div
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: 2,
                      background: color,
                      flexShrink: 0,
                    }}
                  />
                  <div className="flex-1 min-w-0">
                    <div
                      style={{
                        fontSize: 13,
                        color: "var(--text-primary)",
                        fontFamily: "var(--font-mono)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      #{t.trackId} {t.label}
                    </div>
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--text-muted)",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      t={t.bestFrame.timestamp_s.toFixed(2)}s ·{" "}
                      {t.frameCount} frame{t.frameCount === 1 ? "" : "s"} ·{" "}
                      {(t.bestScore * 100).toFixed(0)}%
                    </div>
                  </div>
                  <ZoomIn size={13} style={{ color: "var(--text-muted)" }} />
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function PlaygroundPage() {
  const navigate = useNavigate();

  // Source data
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
  const [projectId, setProjectId] = useState<string>("");
  const effectiveProject = projectId || projects?.[0]?.id || "";
  const { data: videos } = useQuery<VideoOut[]>({
    queryKey: ["videos", effectiveProject],
    queryFn: () => listProjectVideos(effectiveProject),
    enabled: !!effectiveProject,
  });
  const [videoId, setVideoId] = useState<string>("");
  const effectiveVideo = videoId || videos?.[0]?.id || "";
  const currentVideo = videos?.find((v) => v.id === effectiveVideo);
  const videoDuration = currentVideo?.duration_s ?? 30;

  // Controls
  const [prompts, setPrompts] = useState<PromptDraft[]>([{ id: 1, value: "" }]);
  const [threshold, setThreshold] = useState(0.35);
  const [startSec, setStartSec] = useState(0);
  const [durationSec, setDurationSec] = useState(3);
  const [sampleFps, setSampleFps] = useState(4);

  // Result state
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<PreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const validPrompts = prompts
    .map((p) => p.value.trim())
    .filter((v) => v.length > 0);

  const canRun =
    !!effectiveVideo &&
    validPrompts.length > 0 &&
    durationSec > 0 &&
    !running;

  const handleRun = async () => {
    setError(null);
    setResult(null);
    setRunning(true);
    try {
      const res = await previewPrompts({
        videoId: effectiveVideo,
        prompts: validPrompts,
        threshold,
        startSec,
        durationSec,
        sampleFps,
        maxFrames: Math.min(120, Math.ceil(durationSec * sampleFps)),
      });
      setResult(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const handlePromote = async () => {
    if (!effectiveVideo || validPrompts.length === 0) return;
    try {
      const cp = validPrompts.map((p) => ({ name: p, prompt: p }));
      const res = await startLabeling({
        videoId: effectiveVideo,
        classPrompts: cp,
        taskType: "segment",
      });
      navigate(`/review/${res.job_id}`);
    } catch (e: unknown) {
      setError(
        "Failed to start labeling job: " +
          (e instanceof Error ? e.message : String(e))
      );
    }
  };

  // Summary stats derived from result
  const perPromptCounts = useMemo(() => {
    if (!result) return [] as { label: string; count: number }[];
    const m = new Map<string, number>();
    for (const f of result.frames)
      for (const d of f.detections)
        m.set(d.label, (m.get(d.label) ?? 0) + 1);
    return Array.from(m.entries())
      .map(([label, count]) => ({ label, count }))
      .sort((a, b) => b.count - a.count);
  }, [result]);

  return (
    <div className="min-h-screen">
      <div className="max-w-6xl mx-auto mt-8 px-6 pb-16">
        <p className="eyebrow" style={{ marginBottom: 4 }}>
          Prompt iteration
        </p>
        <h1
          className="text-3xl font-bold mb-2 flex items-center gap-3"
          style={{ color: "var(--text-primary)", fontFamily: "var(--font-serif)" }}
        >
          <Wand2 size={26} style={{ color: "var(--accent)" }} />
          SAM 3.1 Playground
        </h1>
        <p className="mb-8" style={{ color: "var(--text-secondary)", maxWidth: 700 }}>
          Try prompts and thresholds on a short contiguous window of your video
          before committing to a full labeling job. The preview runs tracking
          across frames so you can verify that SAM 3.1 will correctly dedupe
          moving objects — not just find them in single frames.
        </p>

        <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6">
          {/* ── LEFT: Controls ── */}
          <div
            className="surface p-5 space-y-5"
            style={{ borderRadius: "var(--radius-lg)", alignSelf: "start" }}
          >
            <div>
              <label className="eyebrow block mb-1.5">Collection</label>
              <select
                value={effectiveProject}
                onChange={(e) => {
                  setProjectId(e.target.value);
                  setVideoId("");
                  setResult(null);
                }}
                className="rounded-lg px-3 py-2 text-sm w-full outline-none"
                style={{
                  border: "1px solid var(--border-default)",
                  backgroundColor: "var(--bg-inset)",
                  color: "var(--text-primary)",
                }}
              >
                {!projects?.length && <option value="">No collections yet</option>}
                {projects?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {p.video_count} video
                    {p.video_count === 1 ? "" : "s"}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="eyebrow block mb-1.5">Video</label>
              <select
                value={effectiveVideo}
                onChange={(e) => {
                  setVideoId(e.target.value);
                  setResult(null);
                }}
                disabled={!videos?.length}
                className="rounded-lg px-3 py-2 text-sm w-full outline-none disabled:opacity-50"
                style={{
                  border: "1px solid var(--border-default)",
                  backgroundColor: "var(--bg-inset)",
                  color: "var(--text-primary)",
                }}
              >
                {!videos?.length && <option value="">No videos</option>}
                {videos?.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.filename}
                    {v.duration_s ? ` · ${v.duration_s.toFixed(1)}s` : ""}
                  </option>
                ))}
              </select>
              {currentVideo && (
                <p
                  className="mt-1.5"
                  style={{
                    fontSize: 11,
                    color: "var(--text-muted)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {currentVideo.width ?? "?"}×{currentVideo.height ?? "?"} ·{" "}
                  {currentVideo.fps ? `${currentVideo.fps.toFixed(1)} fps` : "fps?"}
                </p>
              )}
            </div>

            <div>
              <label className="eyebrow block mb-1.5">Prompts</label>
              <div className="space-y-2">
                {prompts.map((p, i) => (
                  <div key={p.id} className="flex gap-2">
                    <input
                      type="text"
                      value={p.value}
                      onChange={(e) => {
                        const next = [...prompts];
                        next[i] = { ...p, value: e.target.value };
                        setPrompts(next);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && canRun) handleRun();
                      }}
                      placeholder={
                        i === 0 ? "pothole" : "add another class (optional)"
                      }
                      className="flex-1 rounded-lg px-3 py-2 text-sm outline-none"
                      style={{
                        border: "1px solid var(--border-default)",
                        backgroundColor: "var(--bg-inset)",
                        color: "var(--text-primary)",
                      }}
                    />
                    {prompts.length > 1 && (
                      <button
                        onClick={() =>
                          setPrompts(prompts.filter((x) => x.id !== p.id))
                        }
                        className="px-2 rounded-lg"
                        style={{
                          border: "1px solid var(--border-default)",
                          color: "var(--text-muted)",
                        }}
                        aria-label="Remove prompt"
                      >
                        <X size={14} />
                      </button>
                    )}
                  </div>
                ))}
                <button
                  onClick={() =>
                    setPrompts([
                      ...prompts,
                      { id: (prompts.at(-1)?.id ?? 0) + 1, value: "" },
                    ])
                  }
                  className="flex items-center gap-1.5 text-xs"
                  style={{ color: "var(--accent)" }}
                >
                  <Plus size={12} /> Add prompt
                </button>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="eyebrow">Confidence threshold</span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    color: "var(--text-secondary)",
                  }}
                >
                  {threshold.toFixed(2)}
                </span>
              </div>
              <input
                type="range"
                min={0.05}
                max={0.9}
                step={0.01}
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
                className="w-full"
              />
              <p
                style={{
                  fontSize: 10,
                  color: "var(--text-muted)",
                  marginTop: 4,
                }}
              >
                Higher = fewer but more confident detections. Start ~0.35 and
                tune.
              </p>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="eyebrow">Start time</span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    color: "var(--text-secondary)",
                  }}
                >
                  {startSec.toFixed(1)}s
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={Math.max(0, videoDuration - 1)}
                step={0.5}
                value={startSec}
                onChange={(e) => setStartSec(Number(e.target.value))}
                className="w-full"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="eyebrow">Window duration</span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                    color: "var(--text-secondary)",
                  }}
                >
                  {durationSec.toFixed(1)}s
                </span>
              </div>
              <input
                type="range"
                min={1}
                max={Math.min(15, Math.max(1, videoDuration - startSec))}
                step={0.5}
                value={durationSec}
                onChange={(e) => setDurationSec(Number(e.target.value))}
                className="w-full"
              />
              <p
                style={{
                  fontSize: 10,
                  color: "var(--text-muted)",
                  marginTop: 4,
                  display: "flex",
                  alignItems: "start",
                  gap: 4,
                }}
              >
                <Info size={10} className="mt-[1px] shrink-0" />
                Contiguous window, not strided. Tracker will persist IDs across
                this range so you see dedupe behavior.
              </p>
            </div>

            <div>
              <label className="eyebrow block mb-1.5">Sample rate</label>
              <div className="flex gap-1.5">
                {[1, 2, 4, 8].map((f) => (
                  <button
                    key={f}
                    onClick={() => setSampleFps(f)}
                    className="flex-1 py-1.5 rounded-lg text-xs"
                    style={
                      sampleFps === f
                        ? {
                            backgroundColor: "var(--accent)",
                            color: "var(--bg-page)",
                            border: "1px solid var(--accent)",
                            fontWeight: 600,
                          }
                        : {
                            backgroundColor: "transparent",
                            color: "var(--text-secondary)",
                            border: "1px solid var(--border-default)",
                          }
                    }
                  >
                    {f} fps
                  </button>
                ))}
              </div>
              <p
                style={{
                  fontSize: 10,
                  color: "var(--text-muted)",
                  marginTop: 4,
                }}
              >
                ≈ {Math.ceil(durationSec * sampleFps)} frames this run
              </p>
            </div>

            <button
              onClick={handleRun}
              disabled={!canRun}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg disabled:opacity-50"
              style={{
                backgroundColor: "var(--accent)",
                color: "var(--bg-page)",
                fontWeight: 600,
              }}
            >
              {running ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  Running SAM 3.1…
                </>
              ) : (
                <>
                  <Play size={16} />
                  Run preview
                </>
              )}
            </button>
            {error && (
              <p
                className="text-xs"
                style={{ color: "var(--danger)", lineHeight: 1.4 }}
              >
                {error}
              </p>
            )}
          </div>

          {/* ── RIGHT: Results ── */}
          <div>
            {!result && !running && (
              <div
                className="surface p-8 text-center"
                style={{
                  borderRadius: "var(--radius-lg)",
                  color: "var(--text-muted)",
                  borderStyle: "dashed",
                }}
              >
                <Wand2 size={28} style={{ margin: "0 auto 12px", opacity: 0.5 }} />
                <p
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontSize: 18,
                    color: "var(--text-secondary)",
                  }}
                >
                  Pick a video and prompts, then Run preview
                </p>
                <p style={{ fontSize: 13, marginTop: 6 }}>
                  First call warms SAM 3.1 (~10–20s). Subsequent runs are much
                  faster.
                </p>
              </div>
            )}

            {running && (
              <div
                className="surface p-10 text-center"
                style={{ borderRadius: "var(--radius-lg)" }}
              >
                <Loader2
                  size={32}
                  className="animate-spin"
                  style={{ margin: "0 auto 12px", color: "var(--accent)" }}
                />
                <p
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontSize: 17,
                    color: "var(--text-primary)",
                  }}
                >
                  Running SAM 3.1 on {Math.ceil(durationSec * sampleFps)} frames
                </p>
                <p
                  style={{
                    fontSize: 12,
                    color: "var(--text-muted)",
                    marginTop: 4,
                  }}
                >
                  Tracking objects across the window…
                </p>
              </div>
            )}

            {result && (
              <div className="space-y-4">
                {/* Summary bar */}
                <div
                  className="surface p-4 flex flex-wrap items-center gap-6"
                  style={{ borderRadius: "var(--radius-lg)" }}
                >
                  <Stat
                    label="Frames processed"
                    value={result.frames.length.toString()}
                  />
                  <Stat
                    label="Total detections"
                    value={result.total_detections.toString()}
                  />
                  <Stat
                    label="Unique tracked objects"
                    value={result.unique_track_count.toString()}
                    accent
                  />
                  {perPromptCounts.length > 0 && (
                    <div className="flex flex-wrap gap-2 ml-auto">
                      {perPromptCounts.map((p) => (
                        <span
                          key={p.label}
                          className="px-2.5 py-1 rounded-full text-xs"
                          style={{
                            backgroundColor: `hsl(${hashHue(p.label)} 80% 20%)`,
                            color: `hsl(${hashHue(p.label)} 80% 75%)`,
                            border: `1px solid hsl(${hashHue(p.label)} 80% 40%)`,
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {p.label} · {p.count}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Promote CTA */}
                {result.total_detections > 0 && (
                  <div
                    className="flex items-center justify-between gap-4 p-4 rounded-lg"
                    style={{
                      backgroundColor: "var(--accent-soft)",
                      border: "1px solid var(--accent)",
                    }}
                  >
                    <div style={{ fontSize: 13, color: "var(--text-primary)" }}>
                      Happy with this result?{" "}
                      <span style={{ color: "var(--text-secondary)" }}>
                        Run a full labeling job on this video with the same
                        prompts.
                      </span>
                    </div>
                    <button
                      onClick={handlePromote}
                      className="flex items-center gap-2 px-4 py-2 rounded-lg whitespace-nowrap"
                      style={{
                        backgroundColor: "var(--accent)",
                        color: "var(--bg-page)",
                        fontWeight: 600,
                        fontSize: 13,
                      }}
                    >
                      <Rocket size={14} />
                      Start full job
                    </button>
                  </div>
                )}

                {/* Video player with timeline + zoom-to-detection */}
                {result.frames.length > 0 && currentVideo?.url ? (
                  <PreviewPlayer
                    result={result}
                    videoUrl={currentVideo.url}
                    startSec={startSec}
                    durationSec={durationSec}
                  />
                ) : (
                  <div
                    className="surface p-6 text-center"
                    style={{
                      borderRadius: "var(--radius-lg)",
                      color: "var(--text-muted)",
                    }}
                  >
                    {currentVideo?.url
                      ? "No frames returned. Try a longer window or lower threshold."
                      : "Video URL unavailable — reload and retry."}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div
        style={{
          fontSize: 24,
          fontWeight: 700,
          fontFamily: "var(--font-serif)",
          color: accent ? "var(--accent)" : "var(--text-primary)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
    </div>
  );
}
