import { useQuery, useQueryClient } from "@tanstack/react-query";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getJobStats,
  getJobStatus,
  listAnnotations,
  updateAnnotation,
  type AnnotationOut,
} from "../api";
import AnnotationCanvas from "../components/AnnotationCanvas";
import { classColor, hslToHex } from "../components/AnnotationOverlay";
import StatsPanel from "../components/StatsPanel";
import { Keyboard, CheckCheck, XCircle, ChevronLeft, ChevronRight, Filter, Maximize2 } from "lucide-react";

/** Canvas-based annotation overlay — renders polygons + labels at pixel resolution (not SVG). */
const FrameOverlay = React.memo(function FrameOverlay({ annotations, hoveredId }: { annotations: AnnotationOut[]; hoveredId: string | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const W = rect.width;
    const H = rect.height;

    for (const ann of annotations) {
      if (!ann.polygon || ann.polygon.length < 6) continue;
      const isHovered = ann.id === hoveredId;
      const color = ann.status === "accepted" ? "#22c55e" : ann.status === "rejected" ? "#ef4444" : hslToHex(classColor(ann.class_name));

      // Polygon
      ctx.beginPath();
      for (let i = 0; i < ann.polygon.length; i += 2) {
        const px = ann.polygon[i] * W;
        const py = ann.polygon[i + 1] * H;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fillStyle = color + (isHovered ? "55" : "33");
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = isHovered ? 2.5 : 1.5;
      if (ann.status === "rejected") ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.setLineDash([]);

      // Label — same method as AnnotationCanvas (pixel-based)
      let minY = Infinity, labelX = 0;
      for (let i = 0; i < ann.polygon.length; i += 2) {
        const py = ann.polygon[i + 1] * H;
        if (py < minY) { minY = py; labelX = ann.polygon[i] * W; }
      }
      const label = `${ann.class_name} ${ann.confidence != null ? (ann.confidence * 100).toFixed(0) + "%" : ""}`;
      ctx.font = "bold 11px system-ui, sans-serif";
      const tw = ctx.measureText(label).width;
      const lh = 16;
      const ly = Math.max(lh, minY - 3);
      ctx.fillStyle = color + "dd";
      ctx.fillRect(labelX - 2, ly - lh, tw + 8, lh + 2);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, labelX + 2, ly - 3);
    }
  }, [annotations, hoveredId]);

  return (
    <div ref={containerRef} className="absolute inset-0">
      <canvas ref={canvasRef} className="absolute inset-0" />
    </div>
  );
});

/** Defers FrameOverlay canvas setup until the card scrolls into view (IntersectionObserver). */
function useLazyVisible(rootMargin = "200px"): [React.RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (visible) return; // once visible, stay rendered
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) { setVisible(true); obs.disconnect(); } },
      { rootMargin }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [rootMargin, visible]);
  return [ref, visible];
}

const FRAMES_PER_PAGE = 10;
const STATUS_FILTERS = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "accepted", label: "Accepted" },
  { value: "rejected", label: "Rejected" },
];

interface FrameCardProps {
  frameId: string;
  frameAnns: AnnotationOut[];
  hoveredAnn: string | null;
  focusedIdx: number;
  flatAnnotations: AnnotationOut[];
  annotationRefs: React.RefObject<Map<string, HTMLDivElement>>;
  onInspect: (id: string) => void;
  onFocus: (idx: number) => void;
  onHover: (id: string | null) => void;
  onReview: (id: string, status: string) => void;
}

/** Frame card with IntersectionObserver-gated FrameOverlay — canvas only mounts when visible. */
const LazyFrameCard = React.memo(function LazyFrameCard({
  frameId, frameAnns, hoveredAnn, focusedIdx, flatAnnotations, annotationRefs, onInspect, onFocus, onHover, onReview,
}: FrameCardProps) {
  const [cardRef, inView] = useLazyVisible("300px");
  const first = frameAnns[0];

  return (
    <div
      ref={cardRef}
      className="surface overflow-hidden"
      style={{ borderRadius: "var(--radius-lg)" }}
    >
      <div className="relative group cursor-pointer" onClick={() => first.frame_url && onInspect(frameId)}>
        <div className="absolute top-2 right-2 z-10 opacity-0 group-hover:opacity-100 transition-opacity">
          <span
            className="flex items-center gap-1 px-2 py-1 text-xs rounded-lg backdrop-blur"
            style={{ backgroundColor: "rgba(0,0,0,0.7)", color: "#fff" }}
          >
            <Maximize2 size={12} /> Inspect
          </span>
        </div>
        {first.frame_url && (
          <img src={first.frame_url} className="block w-full" loading="lazy" />
        )}
        {inView && <FrameOverlay annotations={frameAnns} hoveredId={hoveredAnn} />}
      </div>

      {/* Per-frame annotation list */}
      <div className="p-3 space-y-1" style={{ backgroundColor: "var(--bg-inset)" }}>
        {frameAnns.map((a) => {
          const globalIdx = flatAnnotations.indexOf(a);
          const isFocused = globalIdx === focusedIdx;
          return (
            <div
              key={a.id}
              ref={(el) => {
                if (el) annotationRefs.current!.set(a.id, el);
                else annotationRefs.current!.delete(a.id);
              }}
              className="flex items-center justify-between text-sm px-2.5 py-2 rounded-lg transition-colors cursor-pointer"
              style={{
                backgroundColor: isFocused ? "var(--accent-soft)" : hoveredAnn === a.id ? "var(--bg-surface-hover)" : undefined,
                border: isFocused ? "1px solid var(--accent)" : "1px solid transparent",
              }}
              onClick={() => onFocus(globalIdx)}
              onMouseEnter={() => onHover(a.id)}
              onMouseLeave={() => onHover(null)}
            >
              <span className="flex items-center gap-2">
                <span
                  className="inline-block w-3 h-3 rounded-sm shrink-0"
                  style={{
                    backgroundColor:
                      a.status === "accepted" ? "var(--success)"
                        : a.status === "rejected" ? "var(--danger)" : "var(--accent)",
                  }}
                />
                <span className="font-medium" style={{ color: "var(--text-primary)" }}>{a.class_name}</span>
                {a.confidence != null && (
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-muted)" }}>
                    {(a.confidence * 100).toFixed(0)}%
                  </span>
                )}
                <span
                  className="text-xs px-1.5 py-0.5 rounded"
                  style={{
                    backgroundColor:
                      a.status === "accepted" ? "var(--success-soft)"
                        : a.status === "rejected" ? "var(--danger-soft)" : "var(--bg-inset)",
                    color:
                      a.status === "accepted" ? "var(--success)"
                        : a.status === "rejected" ? "var(--danger)" : "var(--text-muted)",
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                  }}
                >
                  {a.status}
                </span>
              </span>
              <div className="flex gap-1.5">
                <button
                  onClick={(e) => { e.stopPropagation(); onReview(a.id, "accepted"); }}
                  className="px-2.5 py-1 rounded text-xs font-medium transition-colors"
                  style={a.status === "accepted"
                    ? { backgroundColor: "var(--success)", color: "#fff" }
                    : { backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }
                  }
                >
                  Accept
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); onReview(a.id, "rejected"); }}
                  className="px-2.5 py-1 rounded text-xs font-medium transition-colors"
                  style={a.status === "rejected"
                    ? { backgroundColor: "var(--danger)", color: "#fff" }
                    : { backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }
                  }
                >
                  Reject
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
});

export default function ReviewPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const queryClient = useQueryClient();
  const [focusedIdx, setFocusedIdx] = useState(-1);
  const [confFilter, setConfFilter] = useState(0);
  const [statusFilter, setStatusFilter] = useState("all");
  const [framePage, setFramePage] = useState(0);
  const [hoveredAnn, setHoveredAnn] = useState<string | null>(null);
  const [inspectFrameId, setInspectFrameId] = useState<string | null>(null);
  const annotationRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const { data: job } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJobStatus(jobId!),
    enabled: !!jobId,
  });

  const { data: annotations } = useQuery({
    queryKey: ["annotations", jobId],
    queryFn: () => listAnnotations(jobId!, undefined, undefined, 10000),
    enabled: !!jobId,
  });

  const { data: stats } = useQuery({
    queryKey: ["stats", jobId],
    queryFn: () => getJobStats(jobId!),
    enabled: !!jobId,
  });

  const handleReview = async (annotationId: string, status: string) => {
    try {
      await updateAnnotation(annotationId, { status });
    } catch (e) {
      console.error("Failed to update annotation:", e);
    }
    queryClient.invalidateQueries({ queryKey: ["annotations", jobId] });
    queryClient.invalidateQueries({ queryKey: ["stats", jobId] });
  };

  // Filter by confidence + status
  const filtered = annotations?.filter((a) => {
    if (confFilter > 0 && (a.confidence == null || a.confidence < confFilter)) return false;
    if (statusFilter !== "all" && a.status !== statusFilter) return false;
    return true;
  });

  // Count by status (unfiltered by status, but filtered by confidence)
  const confFiltered = annotations?.filter(
    (a) => confFilter === 0 || (a.confidence != null && a.confidence >= confFilter)
  );
  const statusCounts = {
    all: confFiltered?.length || 0,
    pending: confFiltered?.filter((a) => a.status === "pending").length || 0,
    accepted: confFiltered?.filter((a) => a.status === "accepted").length || 0,
    rejected: confFiltered?.filter((a) => a.status === "rejected").length || 0,
  };

  // Group annotations by frame
  const byFrame = new Map<string, AnnotationOut[]>();
  filtered?.forEach((a) => {
    const group = byFrame.get(a.frame_id) || [];
    group.push(a);
    byFrame.set(a.frame_id, group);
  });

  const frameEntries = Array.from(byFrame.entries());
  const totalPages = Math.ceil(frameEntries.length / FRAMES_PER_PAGE);
  const visibleFrames = frameEntries.slice(
    framePage * FRAMES_PER_PAGE,
    (framePage + 1) * FRAMES_PER_PAGE
  );

  // Flat list for keyboard nav
  const flatAnnotations = filtered || [];

  // Bulk actions
  const handleBulkAccept = useCallback(async () => {
    if (!filtered) return;
    const pending = filtered.filter((a) => a.status === "pending");
    const results = await Promise.allSettled(
      pending.map((a) => updateAnnotation(a.id, { status: "accepted" }))
    );
    const failed = results.filter((r) => r.status === "rejected").length;
    if (failed > 0) console.error(`${failed} annotation(s) failed to accept`);
    queryClient.invalidateQueries({ queryKey: ["annotations", jobId] });
    queryClient.invalidateQueries({ queryKey: ["stats", jobId] });
  }, [filtered, jobId, queryClient]);

  const handleBulkRejectLowConf = useCallback(async () => {
    if (!confFiltered) return;
    const lowConf = confFiltered.filter(
      (a) => a.status === "pending" && a.confidence != null && a.confidence < 0.5
    );
    const results = await Promise.allSettled(
      lowConf.map((a) => updateAnnotation(a.id, { status: "rejected" }))
    );
    const failed = results.filter((r) => r.status === "rejected").length;
    if (failed > 0) console.error(`${failed} annotation(s) failed to reject`);
    queryClient.invalidateQueries({ queryKey: ["annotations", jobId] });
    queryClient.invalidateQueries({ queryKey: ["stats", jobId] });
  }, [confFiltered, jobId, queryClient]);

  // Jump to next pending frame
  const jumpToNextPending = useCallback(() => {
    if (!annotations) return;
    const pendingFrames = new Set<string>();
    annotations.forEach((a) => { if (a.status === "pending") pendingFrames.add(a.frame_id); });
    const allFrameEntries = Array.from(byFrame.entries());
    const startIdx = (framePage + 1) * FRAMES_PER_PAGE;
    for (let i = startIdx; i < allFrameEntries.length; i++) {
      if (pendingFrames.has(allFrameEntries[i][0])) {
        setFramePage(Math.floor(i / FRAMES_PER_PAGE));
        return;
      }
    }
    for (let i = 0; i < startIdx && i < allFrameEntries.length; i++) {
      if (pendingFrames.has(allFrameEntries[i][0])) {
        setFramePage(Math.floor(i / FRAMES_PER_PAGE));
        return;
      }
    }
  }, [annotations, byFrame, framePage]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        setFocusedIdx((i) => {
          const next = Math.min(i + 1, flatAnnotations.length - 1);
          const ann = flatAnnotations[next];
          if (ann) annotationRefs.current.get(ann.id)?.scrollIntoView({ behavior: "smooth", block: "center" });
          return next;
        });
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        setFocusedIdx((i) => {
          const next = Math.max(i - 1, 0);
          const ann = flatAnnotations[next];
          if (ann) annotationRefs.current.get(ann.id)?.scrollIntoView({ behavior: "smooth", block: "center" });
          return next;
        });
      } else if (e.key === "a" && focusedIdx >= 0 && focusedIdx < flatAnnotations.length) {
        handleReview(flatAnnotations[focusedIdx].id, "accepted");
      } else if (e.key === "r" && focusedIdx >= 0 && focusedIdx < flatAnnotations.length) {
        handleReview(flatAnnotations[focusedIdx].id, "rejected");
      } else if (e.key === "n") {
        jumpToNextPending();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [flatAnnotations, focusedIdx, jumpToNextPending]);

  const acceptedCount = annotations?.filter((a) => a.status === "accepted").length || 0;
  const pendingCount = annotations?.filter((a) => a.status === "pending").length || 0;

  return (
    <div className="min-h-screen pb-20">

      <div className="max-w-6xl mx-auto mt-8 px-4">
        {/* Header */}
        <div className="flex justify-between items-start mb-6">
          <div>
            <p className="eyebrow" style={{ marginBottom: 4 }}>Annotation review</p>
            <h1 className="text-2xl font-bold" style={{ color: "var(--text-primary)" }}>Review Labels</h1>
            {job && (
              <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
                {job.name || job.text_prompt || "Exemplar"} &middot; {job.status}
                {job.result_url && (
                  <>
                    {" "}&middot;{" "}
                    <a href={job.result_url} className="hover:underline" style={{ color: "var(--accent)" }}>
                      Download
                    </a>
                  </>
                )}
              </p>
            )}
          </div>
          {job && (
            <Link
              to={`/train/${jobId}`}
              className="px-4 py-2 text-white rounded-lg text-sm"
              style={{ backgroundColor: "var(--accent)" }}
            >
              Train Model
            </Link>
          )}
        </div>

        {/* Status filter tabs */}
        <div className="flex items-center gap-1 mb-4">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => { setStatusFilter(f.value); setFramePage(0); }}
              className="px-3 py-1.5 rounded-md text-sm transition-colors"
              style={statusFilter === f.value
                ? { backgroundColor: "var(--text-primary)", color: "var(--bg-page)" }
                : { backgroundColor: "var(--bg-inset)", color: "var(--text-secondary)" }
              }
            >
              {f.label}
              <span className="ml-1.5" style={{ opacity: 0.6, fontFamily: "var(--font-mono)", fontSize: 11 }}>
                {statusCounts[f.value as keyof typeof statusCounts]}
              </span>
            </button>
          ))}
          {statusCounts.pending > 0 && statusFilter !== "pending" && (
            <button
              onClick={() => { setStatusFilter("pending"); setFramePage(0); }}
              className="ml-2 flex items-center gap-1 px-3 py-1.5 rounded-md text-sm"
              style={{ backgroundColor: "var(--warning-soft)", border: "1px solid var(--warning)", color: "var(--warning)" }}
            >
              <Filter size={12} />
              Show {statusCounts.pending} needing review
            </button>
          )}
        </div>

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-3 mb-5 pb-4" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
          <button
            onClick={handleBulkAccept}
            disabled={pendingCount === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm disabled:opacity-40"
            style={{ backgroundColor: "var(--success-soft)", border: "1px solid var(--success)", color: "var(--success)" }}
          >
            <CheckCheck size={14} />
            Accept All ({pendingCount})
          </button>
          <button
            onClick={handleBulkRejectLowConf}
            disabled={!annotations?.some((a) => a.status === "pending" && a.confidence != null && a.confidence < 0.5)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm disabled:opacity-40"
            style={{ backgroundColor: "var(--danger-soft)", border: "1px solid var(--danger)", color: "var(--danger)" }}
          >
            <XCircle size={14} />
            Reject &lt;50%
          </button>
          <button
            onClick={jumpToNextPending}
            disabled={pendingCount === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm disabled:opacity-40"
            style={{ backgroundColor: "var(--accent-soft)", border: "1px solid var(--accent)", color: "var(--accent)" }}
          >
            Next Pending
          </button>

          <div className="flex items-center gap-2 ml-auto">
            <label className="eyebrow" style={{ fontSize: 10 }}>Min conf</label>
            <input
              type="range" min={0} max={1} step={0.05} value={confFilter}
              onChange={(e) => { setConfFilter(Number(e.target.value)); setFramePage(0); }}
              className="w-24"
            />
            <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)", width: 28, textAlign: "right" }}>
              {confFilter > 0 ? `${(confFilter * 100).toFixed(0)}%` : "All"}
            </span>
          </div>

          <div className="flex items-center gap-1.5 pl-3" style={{ borderLeft: "1px solid var(--border-subtle)", color: "var(--text-muted)", fontSize: 11 }}>
            <Keyboard size={11} />
            {["A accept", "R reject", "N next", "\u2191\u2193 nav"].map((hint) => (
              <span key={hint} className="px-1 py-0.5 rounded" style={{ backgroundColor: "var(--bg-inset)", fontFamily: "var(--font-mono)", fontSize: 10 }}>
                {hint}
              </span>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-[1fr_280px] gap-6">
          {/* Annotation grid */}
          <div className="space-y-4">
            {filtered?.length === 0 && (
              <p className="py-8 text-center" style={{ color: "var(--text-muted)" }}>
                {statusFilter === "pending"
                  ? "No pending annotations — all reviewed!"
                  : "No annotations match the current filters."}
              </p>
            )}

            {/* Frame pagination */}
            {frameEntries.length > FRAMES_PER_PAGE && (
              <div className="flex items-center justify-between text-sm pb-2" style={{ color: "var(--text-muted)" }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                  Frames {framePage * FRAMES_PER_PAGE + 1}&ndash;
                  {Math.min((framePage + 1) * FRAMES_PER_PAGE, frameEntries.length)} of {frameEntries.length}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setFramePage((p) => Math.max(0, p - 1))}
                    disabled={framePage === 0}
                    className="p-1 rounded disabled:opacity-30"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    <ChevronLeft size={16} />
                  </button>
                  {totalPages <= 10 ? (
                    Array.from({ length: totalPages }, (_, i) => (
                      <button
                        key={i}
                        onClick={() => setFramePage(i)}
                        className="w-7 h-7 rounded text-xs"
                        style={i === framePage
                          ? { backgroundColor: "var(--text-primary)", color: "var(--bg-page)", fontFamily: "var(--font-mono)" }
                          : { color: "var(--text-secondary)", fontFamily: "var(--font-mono)" }
                        }
                      >
                        {i + 1}
                      </button>
                    ))
                  ) : (
                    <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", padding: "0 8px" }}>
                      {framePage + 1}/{totalPages}
                    </span>
                  )}
                  <button
                    onClick={() => setFramePage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={framePage >= totalPages - 1}
                    className="p-1 rounded disabled:opacity-30"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    <ChevronRight size={16} />
                  </button>
                </div>
              </div>
            )}

            {visibleFrames.map(([frameId, frameAnns]) => (
              <LazyFrameCard
                key={frameId}
                frameId={frameId}
                frameAnns={frameAnns}
                hoveredAnn={hoveredAnn}
                focusedIdx={focusedIdx}
                flatAnnotations={flatAnnotations}
                annotationRefs={annotationRefs}
                onInspect={setInspectFrameId}
                onFocus={setFocusedIdx}
                onHover={setHoveredAnn}
                onReview={handleReview}
              />
            ))}
          </div>

          {/* Stats sidebar */}
          <div className="sticky top-4">
            {stats && <StatsPanel stats={stats} />}
          </div>
        </div>
      </div>

      {/* Sticky train bar — show after >50% reviewed */}
      {job && acceptedCount > 0 && annotations && (acceptedCount + (annotations.filter((a) => a.status === "rejected").length)) > annotations.length * 0.5 && (
        <div
          className="fixed bottom-0 left-0 right-0 py-3 px-6 flex items-center justify-between z-50"
          style={{ backgroundColor: "var(--success)", color: "#fff" }}
        >
          <span className="text-sm">
            <strong>{acceptedCount}</strong> accepted, <strong>{pendingCount}</strong> remaining to review.
            {pendingCount === 0 && " All reviewed!"}
          </span>
          <Link
            to={`/train/${jobId}`}
            className="px-5 py-2 rounded-lg text-sm font-medium"
            style={{ backgroundColor: "#fff", color: "var(--success)" }}
          >
            Train Model
          </Link>
        </div>
      )}

      {/* Full-screen annotation inspector */}
      {inspectFrameId && byFrame.has(inspectFrameId) && (() => {
        const frameAnns = byFrame.get(inspectFrameId)!;
        const frameUrl = frameAnns[0]?.frame_url;
        if (!frameUrl) return null;

        const allFrameIds = frameEntries.map(([id]) => id);
        const currentIdx = allFrameIds.indexOf(inspectFrameId);
        const allClasses = [...new Set(annotations?.map((a) => a.class_name) || [])].sort();

        return (
          <AnnotationCanvas
            imageUrl={frameUrl}
            frameId={inspectFrameId}
            jobId={jobId!}
            annotations={frameAnns}
            classes={allClasses}
            onAccept={(id) => handleReview(id, "accepted")}
            onReject={(id) => handleReview(id, "rejected")}
            onClose={() => setInspectFrameId(null)}
            onPrev={currentIdx > 0 ? () => setInspectFrameId(allFrameIds[currentIdx - 1]) : undefined}
            onNext={currentIdx < allFrameIds.length - 1 ? () => setInspectFrameId(allFrameIds[currentIdx + 1]) : undefined}
            onAnnotationCreated={() => {
              queryClient.invalidateQueries({ queryKey: ["annotations", jobId] });
              queryClient.invalidateQueries({ queryKey: ["stats", jobId] });
            }}
          />
        );
      })()}
    </div>
  );
}
