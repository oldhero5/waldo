import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getJobStats,
  getJobStatus,
  listAnnotations,
  updateAnnotation,
  type AnnotationOut,
} from "../api";
import AnnotationCanvas from "../components/AnnotationCanvas";
import AnnotationOverlay from "../components/AnnotationOverlay";
import StatsPanel from "../components/StatsPanel";
import { Keyboard, CheckCheck, XCircle, ChevronLeft, ChevronRight, Filter, Maximize2 } from "lucide-react";

const FRAMES_PER_PAGE = 10;
const STATUS_FILTERS = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "accepted", label: "Accepted" },
  { value: "rejected", label: "Rejected" },
];

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
    queryFn: () => listAnnotations(jobId!),
    enabled: !!jobId,
  });

  const { data: stats } = useQuery({
    queryKey: ["stats", jobId],
    queryFn: () => getJobStats(jobId!),
    enabled: !!jobId,
  });

  const handleReview = async (annotationId: string, status: string) => {
    await updateAnnotation(annotationId, { status });
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
    await Promise.all(pending.map((a) => updateAnnotation(a.id, { status: "accepted" })));
    queryClient.invalidateQueries({ queryKey: ["annotations", jobId] });
    queryClient.invalidateQueries({ queryKey: ["stats", jobId] });
  }, [filtered, jobId, queryClient]);

  const handleBulkRejectLowConf = useCallback(async () => {
    if (!confFiltered) return;
    const lowConf = confFiltered.filter(
      (a) => a.status === "pending" && a.confidence != null && a.confidence < 0.5
    );
    await Promise.all(lowConf.map((a) => updateAnnotation(a.id, { status: "rejected" })));
    queryClient.invalidateQueries({ queryKey: ["annotations", jobId] });
    queryClient.invalidateQueries({ queryKey: ["stats", jobId] });
  }, [confFiltered, jobId, queryClient]);

  // Jump to next pending frame
  const jumpToNextPending = useCallback(() => {
    if (!annotations) return;
    // Find first frame with pending annotations after current page
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
    // Wrap around
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
        <div className="flex justify-between items-start mb-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Review Labels</h1>
            {job && (
              <p className="text-gray-500 text-sm mt-1">
                {job.text_prompt || "Exemplar"} &middot; {job.status}
                {job.result_url && (
                  <>
                    {" "}&middot;{" "}
                    <a href={job.result_url} className="text-blue-600 hover:underline">
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
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm"
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
              className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                statusFilter === f.value
                  ? "bg-gray-900 text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {f.label}
              <span className="ml-1.5 opacity-60">
                {statusCounts[f.value as keyof typeof statusCounts]}
              </span>
            </button>
          ))}
          {statusCounts.pending > 0 && statusFilter !== "pending" && (
            <button
              onClick={() => { setStatusFilter("pending"); setFramePage(0); }}
              className="ml-2 flex items-center gap-1 px-3 py-1.5 bg-amber-50 text-amber-700 border border-amber-200 rounded-md text-sm hover:bg-amber-100"
            >
              <Filter size={12} />
              Show {statusCounts.pending} needing review
            </button>
          )}
        </div>

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-3 mb-5 pb-4 border-b border-gray-100">
          <button
            onClick={handleBulkAccept}
            disabled={pendingCount === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-green-50 text-green-700 border border-green-200 rounded-lg text-sm hover:bg-green-100 disabled:opacity-40"
          >
            <CheckCheck size={14} />
            Accept All Pending ({pendingCount})
          </button>
          <button
            onClick={handleBulkRejectLowConf}
            disabled={!annotations?.some((a) => a.status === "pending" && a.confidence != null && a.confidence < 0.5)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-50 text-red-700 border border-red-200 rounded-lg text-sm hover:bg-red-100 disabled:opacity-40"
          >
            <XCircle size={14} />
            Reject Below 50%
          </button>
          <button
            onClick={jumpToNextPending}
            disabled={pendingCount === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-50 text-blue-700 border border-blue-200 rounded-lg text-sm hover:bg-blue-100 disabled:opacity-40"
          >
            Next Pending
          </button>
          <div className="flex items-center gap-2 ml-auto">
            <label className="text-xs text-gray-500">Min confidence:</label>
            <input
              type="range" min={0} max={1} step={0.05} value={confFilter}
              onChange={(e) => { setConfFilter(Number(e.target.value)); setFramePage(0); }}
              className="w-24"
            />
            <span className="text-xs font-mono text-gray-500 w-8">
              {confFilter > 0 ? `${(confFilter * 100).toFixed(0)}%` : "All"}
            </span>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-gray-400 border-l border-gray-200 pl-3">
            <Keyboard size={12} />
            <span>
              <kbd className="px-1 py-0.5 bg-gray-100 rounded text-[10px]">A</kbd> accept
              <kbd className="px-1 py-0.5 bg-gray-100 rounded text-[10px] ml-1">R</kbd> reject
              <kbd className="px-1 py-0.5 bg-gray-100 rounded text-[10px] ml-1">N</kbd> next pending
              <kbd className="px-1 py-0.5 bg-gray-100 rounded text-[10px] ml-1">&uarr;&darr;</kbd> nav
            </span>
          </div>
        </div>

        <div className="grid grid-cols-[1fr_280px] gap-6">
          {/* Annotation grid */}
          <div className="space-y-4">
            {filtered?.length === 0 && (
              <p className="text-gray-500 py-8 text-center">
                {statusFilter === "pending"
                  ? "No pending annotations — all reviewed!"
                  : "No annotations match the current filters."}
              </p>
            )}

            {/* Frame pagination */}
            {frameEntries.length > FRAMES_PER_PAGE && (
              <div className="flex items-center justify-between text-sm text-gray-500 pb-2">
                <span>
                  Frames {framePage * FRAMES_PER_PAGE + 1}&ndash;
                  {Math.min((framePage + 1) * FRAMES_PER_PAGE, frameEntries.length)} of {frameEntries.length}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setFramePage((p) => Math.max(0, p - 1))}
                    disabled={framePage === 0}
                    className="p-1 rounded hover:bg-gray-100 disabled:opacity-30"
                  >
                    <ChevronLeft size={16} />
                  </button>
                  {/* Page number indicators */}
                  {totalPages <= 10 ? (
                    Array.from({ length: totalPages }, (_, i) => (
                      <button
                        key={i}
                        onClick={() => setFramePage(i)}
                        className={`w-7 h-7 rounded text-xs ${
                          i === framePage ? "bg-gray-900 text-white" : "hover:bg-gray-100"
                        }`}
                      >
                        {i + 1}
                      </button>
                    ))
                  ) : (
                    <span className="text-xs px-2">Page {framePage + 1}/{totalPages}</span>
                  )}
                  <button
                    onClick={() => setFramePage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={framePage >= totalPages - 1}
                    className="p-1 rounded hover:bg-gray-100 disabled:opacity-30"
                  >
                    <ChevronRight size={16} />
                  </button>
                </div>
              </div>
            )}

            {visibleFrames.map(([frameId, frameAnns]) => {
              const first = frameAnns[0];
              return (
                <div key={frameId} className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                  <div className="relative group cursor-pointer" onClick={() => first.frame_url && setInspectFrameId(frameId)}>
                    {/* Inspect button overlay */}
                    <div className="absolute top-2 right-2 z-10 opacity-0 group-hover:opacity-100 transition-opacity">
                      <span className="flex items-center gap-1 px-2 py-1 bg-gray-900/80 text-white text-xs rounded-lg backdrop-blur">
                        <Maximize2 size={12} /> Inspect
                      </span>
                    </div>
                    {first.frame_url && (
                      <img src={first.frame_url} className="block w-full" loading="lazy" />
                    )}
                    <svg
                      className="absolute inset-0 w-full h-full"
                      viewBox="0 0 1 1"
                      preserveAspectRatio="none"
                    >
                      {frameAnns.map((a) => (
                        <AnnotationOverlay
                          key={a.id}
                          polygon={a.polygon}
                          status={a.status}
                          className={a.class_name}
                          label={`${a.class_name} ${a.confidence != null ? (a.confidence * 100).toFixed(0) + "%" : ""}`}
                          highlight={hoveredAnn === a.id}
                        />
                      ))}
                    </svg>
                  </div>
                  <div className="p-3 bg-gray-50 space-y-1">
                    {frameAnns.map((a) => {
                      const globalIdx = flatAnnotations.indexOf(a);
                      const isFocused = globalIdx === focusedIdx;
                      return (
                        <div
                          key={a.id}
                          ref={(el) => {
                            if (el) annotationRefs.current.set(a.id, el);
                            else annotationRefs.current.delete(a.id);
                          }}
                          className={`flex items-center justify-between text-sm px-2 py-1.5 rounded transition-colors cursor-pointer ${
                            isFocused ? "bg-blue-50 ring-1 ring-blue-300" : hoveredAnn === a.id ? "bg-gray-100" : ""
                          }`}
                          onClick={() => setFocusedIdx(globalIdx)}
                          onMouseEnter={() => setHoveredAnn(a.id)}
                          onMouseLeave={() => setHoveredAnn(null)}
                        >
                          <span className="flex items-center gap-2">
                            <span
                              className="inline-block w-3 h-3 rounded-sm shrink-0"
                              style={{
                                backgroundColor:
                                  a.status === "accepted" ? "#22c55e"
                                    : a.status === "rejected" ? "#ef4444" : "#3b82f6",
                              }}
                            />
                            <span className="font-medium">{a.class_name}</span>
                            {a.confidence != null && (
                              <span className="text-gray-400">
                                {(a.confidence * 100).toFixed(0)}%
                              </span>
                            )}
                            <span className={`text-xs px-1.5 py-0.5 rounded ${
                              a.status === "accepted" ? "bg-green-100 text-green-700"
                                : a.status === "rejected" ? "bg-red-100 text-red-700"
                                  : "bg-gray-100 text-gray-500"
                            }`}>
                              {a.status}
                            </span>
                          </span>
                          <div className="flex gap-2">
                            <button
                              onClick={(e) => { e.stopPropagation(); handleReview(a.id, "accepted"); }}
                              className={`px-3 py-1 rounded text-xs font-medium ${
                                a.status === "accepted"
                                  ? "bg-green-600 text-white"
                                  : "bg-gray-100 hover:bg-green-50 hover:text-green-700"
                              }`}
                            >
                              Accept
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); handleReview(a.id, "rejected"); }}
                              className={`px-3 py-1 rounded text-xs font-medium ${
                                a.status === "rejected"
                                  ? "bg-red-600 text-white"
                                  : "bg-gray-100 hover:bg-red-50 hover:text-red-700"
                              }`}
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
            })}
          </div>

          {/* Stats sidebar */}
          <div className="sticky top-4">
            {stats && <StatsPanel stats={stats} />}
          </div>
        </div>
      </div>

      {/* Sticky train bar — show after >50% reviewed */}
      {job && acceptedCount > 0 && annotations && (acceptedCount + (annotations.filter((a) => a.status === "rejected").length)) > annotations.length * 0.5 && (
        <div className="fixed bottom-0 left-0 right-0 bg-green-600 text-white py-3 px-6 flex items-center justify-between z-50">
          <span className="text-sm">
            <strong>{acceptedCount}</strong> accepted, <strong>{pendingCount}</strong> remaining to review.
            {pendingCount === 0 && " All reviewed!"}
          </span>
          <Link
            to={`/train/${jobId}`}
            className="px-5 py-2 bg-white text-green-700 rounded-lg text-sm font-medium hover:bg-green-50"
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

        // Collect unique class names
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
