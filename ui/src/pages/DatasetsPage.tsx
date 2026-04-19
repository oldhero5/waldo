import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { listJobs, getDatasetOverview, uploadImages, uploadVideo, listFeedback, listProjects, listProjectVideos, linkVideos, deleteJob, renameJob, duplicateDataset, mergeClasses, deleteClass, listAnnotations, updateAnnotation, exportDataset, addClassToDataset, type JobStatus } from "../api";
import AnnotationCanvas from "../components/AnnotationCanvas";
import { Database, CheckCircle, Clock, AlertCircle, Download, Eye, Cpu, MessageSquareWarning, Images, Plus, Upload as UploadIcon, Loader, FolderInput, Trash2, Copy, Merge, Tag, Pencil, Check, X, Search, ChevronDown, ArrowUpDown, SquareCheck } from "lucide-react";
import Accordion from "../components/Accordion";

function DatasetAnnotationViewer({ frameId, imageUrl, jobId, classes, onClose, onPrev, onNext }: {
  frameId: string; imageUrl: string; jobId: string; classes: string[];
  onClose: () => void; onPrev?: () => void; onNext?: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: frameAnnotations = [] } = useQuery({
    queryKey: ["annotations", jobId, frameId],
    queryFn: () => listAnnotations(jobId, undefined, frameId),
    staleTime: 30000,
  });

  return (
    <AnnotationCanvas
      imageUrl={imageUrl}
      frameId={frameId}
      jobId={jobId}
      annotations={frameAnnotations}
      classes={classes}
      onAccept={async (id) => { await updateAnnotation(id, { status: "accepted" }); queryClient.invalidateQueries({ queryKey: ["annotations", jobId] }); }}
      onReject={async (id) => { await updateAnnotation(id, { status: "rejected" }); queryClient.invalidateQueries({ queryKey: ["annotations", jobId] }); }}
      onClose={onClose}
      onPrev={onPrev}
      onNext={onNext}
      onAnnotationCreated={() => queryClient.invalidateQueries({ queryKey: ["annotations", jobId] })}
    />
  );
}


function DatasetCard({ job, onDeleted, selected, onToggleSelect }: {
  job: JobStatus; onDeleted: () => void;
  selected?: boolean; onToggleSelect?: () => void;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [galleryIdx, setGalleryIdx] = useState<number | null>(null);
  const [showCorrections, setShowCorrections] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importingFrom, setImportingFrom] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [showExport, setShowExport] = useState(false);
  const [exporting, setExporting] = useState(false);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);

  const displayName = job.name || job.text_prompt || "Exemplar labeling";

  const handleRename = async () => {
    const trimmed = editName.trim();
    if (!trimmed || trimmed === displayName) {
      setEditing(false);
      return;
    }
    try {
      await renameJob(job.job_id, trimmed);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["dataset-overview", job.job_id] });
    } catch (e: any) {
      setUploadMsg(`Error: ${e.message}`);
    }
    setEditing(false);
  };

  const { data: allProjects } = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
    enabled: showImport,
  });

  const { data: importVideos } = useQuery({
    queryKey: ["project-videos", importingFrom],
    queryFn: () => listProjectVideos(importingFrom!),
    enabled: !!importingFrom,
  });

  const collectionName = job.name || job.text_prompt || "default";

  const handleDelete = async () => {
    if (!confirm(`Delete the "${collectionName}" dataset and all its annotations? This cannot be undone.`)) return;
    try {
      await deleteJob(job.job_id);
      onDeleted();
    } catch (e: any) {
      setUploadMsg(`Error: ${e.message}`);
    }
  };

  const handleImportAll = async () => {
    if (!importVideos) return;
    setUploading(true);
    setUploadMsg("");
    try {
      const result = await linkVideos(
        importVideos.map((v) => v.id),
        collectionName
      );
      setUploadMsg(`Linked ${result.linked} video${result.linked !== 1 ? "s" : ""} — auto-labeling ${result.auto_labeled}`);
      setShowImport(false);
      setImportingFrom(null);
    } catch (e: any) {
      setUploadMsg(`Error: ${e.message}`);
    } finally {
      setUploading(false);
    }
  };

  const { data: feedback } = useQuery({
    queryKey: ["feedback"],
    queryFn: () => listFeedback(50),
    enabled: showCorrections,
  });

  const handleImageUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setUploadMsg("");
    try {
      const result = await uploadImages(Array.from(files), collectionName);
      setUploadMsg(`Added ${result.frame_ids.length} image${result.frame_ids.length !== 1 ? "s" : ""} to "${collectionName}"`);
    } catch (e: any) {
      setUploadMsg(`Error: ${e.message}`);
    } finally {
      setUploading(false);
    }
  };

  const handleVideoUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setUploadMsg("");
    let added = 0;
    let skipped = 0;
    const fileArr = Array.from(files);
    const CONCURRENCY = 8;
    // Only update progress state every N completions or at least every 500 ms
    // to avoid thrashing the React scheduler on large batches.
    let lastProgressUpdate = Date.now();
    const UPDATE_INTERVAL_MS = 500;

    try {
      // Process in batches of CONCURRENCY
      for (let i = 0; i < fileArr.length; i += CONCURRENCY) {
        const batch = fileArr.slice(i, i + CONCURRENCY);
        const results = await Promise.allSettled(
          batch.map((file) => uploadVideo(file, collectionName))
        );
        for (const r of results) {
          if (r.status === "fulfilled") {
            added++;
          } else {
            const msg = r.reason?.message || "";
            if (msg.includes("duplicate") || msg.includes("already exists")) {
              skipped++;
            } else {
              throw r.reason;
            }
          }
        }
        // Batch progress updates — at most one setState per 500 ms or on last batch
        const now = Date.now();
        const isLastBatch = i + CONCURRENCY >= fileArr.length;
        if (isLastBatch || now - lastProgressUpdate >= UPDATE_INTERVAL_MS) {
          setUploadMsg(`Uploading... ${added + skipped}/${fileArr.length} videos`);
          lastProgressUpdate = now;
        }
      }
      const parts = [`Added ${added} video${added !== 1 ? "s" : ""} to "${collectionName}"`];
      if (skipped > 0) parts.push(`(${skipped} duplicate${skipped !== 1 ? "s" : ""} skipped)`);
      if (added > 0) parts.push("— auto-labeling started");
      setUploadMsg(parts.join(" "));
    } catch (e: any) {
      const parts = [];
      if (added > 0) parts.push(`Added ${added} video${added !== 1 ? "s" : ""}`);
      parts.push(`Error: ${e.message}`);
      setUploadMsg(parts.join(". "));
    } finally {
      setUploading(false);
    }
  };

  const { data: overview } = useQuery({
    queryKey: ["dataset-overview", job.job_id],
    queryFn: () => getDatasetOverview(job.job_id),
    enabled: expanded,
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.labeling_in_progress > 0 ? 5000 : false;
    },
  });

  const reviewProgress = overview
    ? Math.round(((overview.accepted + overview.rejected) / Math.max(1, overview.total_annotations)) * 100)
    : null;

  const statusColor = job.status === "completed"
    ? "var(--success)" : job.status === "failed"
    ? "var(--danger)" : "var(--accent)";

  const statusBg = job.status === "completed"
    ? "var(--success-soft)" : job.status === "failed"
    ? "var(--danger-soft)" : "var(--accent-soft)";

  return (
    <div className="surface overflow-hidden" style={{ borderRadius: "var(--radius-lg)" }}>
      {/* Card header */}
      <div
        className="p-4 cursor-pointer transition-colors"
        style={{ transition: "background var(--transition)" }}
        onClick={() => setExpanded(!expanded)}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-surface-hover)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "")}
      >
        <div className="flex items-center gap-4">
          {/* Selection checkbox */}
          {onToggleSelect && (
            <input
              type="checkbox"
              checked={selected || false}
              onChange={(e) => { e.stopPropagation(); onToggleSelect(); }}
              onClick={(e) => e.stopPropagation()}
              className="w-4 h-4 shrink-0 rounded"
            />
          )}

          {/* Status indicator */}
          <div
            className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
            style={{ backgroundColor: statusBg }}
          >
            {job.status === "completed" ? <CheckCircle size={20} style={{ color: statusColor }} />
              : job.status === "failed" ? <AlertCircle size={20} style={{ color: statusColor }} />
                : <Clock size={20} style={{ color: statusColor }} />}
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 group/name">
              {editing ? (
                <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                  <input
                    ref={nameInputRef}
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleRename();
                      if (e.key === "Escape") setEditing(false);
                    }}
                    autoFocus
                    className="px-2 py-0.5 rounded-lg border text-sm"
                    style={{
                      fontFamily: "var(--font-serif)",
                      fontWeight: 600,
                      color: "var(--text-primary)",
                      backgroundColor: "var(--bg-inset)",
                      borderColor: "var(--accent)",
                      outline: "none",
                      minWidth: 160,
                    }}
                  />
                  <button
                    onClick={handleRename}
                    style={{ color: "var(--success)", background: "none", border: "none", padding: 2 }}
                    title="Save"
                  >
                    <Check size={14} />
                  </button>
                  <button
                    onClick={() => setEditing(false)}
                    style={{ color: "var(--text-muted)", background: "none", border: "none", padding: 2 }}
                    title="Cancel"
                  >
                    <X size={14} />
                  </button>
                </div>
              ) : (
                <>
                  <h3
                    style={{
                      fontFamily: "var(--font-serif)",
                      fontWeight: 600,
                      color: "var(--text-primary)",
                      fontSize: 15,
                    }}
                  >
                    {displayName}
                  </h3>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditName(displayName);
                      setEditing(true);
                    }}
                    className="opacity-0 group-hover/name:opacity-100 transition-opacity"
                    style={{ color: "var(--text-muted)", background: "none", border: "none", padding: 2 }}
                    title="Rename dataset"
                  >
                    <Pencil size={12} />
                  </button>
                </>
              )}
            </div>
            <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2, fontFamily: "var(--font-mono)" }}>
              {job.total_frames} video{job.total_frames !== 1 ? "s" : ""} &middot; {job.processed_frames} processed
              {job.version > 1 && (
                <span
                  style={{
                    marginLeft: 6,
                    padding: "1px 5px",
                    borderRadius: 4,
                    backgroundColor: "var(--accent-soft)",
                    color: "var(--accent)",
                    fontSize: 10,
                  }}
                >
                  v{job.version}
                </span>
              )}
            </p>
          </div>

          {/* Quick stats */}
          <div className="flex gap-5 text-xs shrink-0">
            <div className="text-center">
              <span className="eyebrow block" style={{ fontSize: 10 }}>Status</span>
              <span className="font-medium capitalize" style={{ color: statusColor }}>{job.status}</span>
            </div>
            <div className="text-center">
              <span className="eyebrow block" style={{ fontSize: 10 }}>Videos</span>
              <span className="font-medium" style={{ color: "var(--text-primary)" }}>{job.total_frames}</span>
            </div>
            {job.annotation_count != null && (
              <div className="text-center">
                <span className="eyebrow block" style={{ fontSize: 10 }}>Labels</span>
                <span className="font-medium" style={{ color: "var(--text-primary)" }}>{job.annotation_count}</span>
              </div>
            )}
            {job.class_count != null && job.class_count > 0 && (
              <div className="text-center">
                <span className="eyebrow block" style={{ fontSize: 10 }}>Classes</span>
                <span className="font-medium" style={{ color: "var(--text-primary)" }}>{job.class_count}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="p-4" style={{ borderTop: "1px solid var(--border-subtle)", backgroundColor: "var(--bg-inset)" }}>
          {!overview ? (
            <p className="py-4 text-center" style={{ fontSize: 13, color: "var(--text-muted)" }}>Loading dataset...</p>
          ) : (
            <>
              {/* Thumbnail grid */}
              {overview.sample_frames.length > 0 && (
                <div className="mb-4">
                  <p className="eyebrow mb-2 flex items-center gap-1">
                    <Images size={12} />
                    Sample frames ({overview.labeled_frames} total)
                  </p>
                  <div className="grid grid-cols-4 sm:grid-cols-6 gap-2">
                    {overview.sample_frames.map((f, idx) => (
                      <button
                        key={f.frame_id}
                        onClick={() => setGalleryIdx(idx)}
                        className="relative group text-left cursor-pointer"
                      >
                        {f.thumbnail_url ? (
                          <img
                            src={f.thumbnail_url}
                            alt={`Frame ${f.frame_number}`}
                            className="w-full aspect-video object-cover rounded-lg transition-colors"
                            style={{ border: "1px solid var(--border-default)" }}
                            loading="lazy"
                            onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
                            onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border-default)")}
                          />
                        ) : (
                          <div className="w-full aspect-video rounded-lg" style={{ backgroundColor: "var(--bg-inset)" }} />
                        )}
                        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent rounded-b-lg px-1.5 py-1">
                          <span style={{ fontSize: 10, color: "#fff", fontWeight: 500, fontFamily: "var(--font-mono)" }}>
                            {f.annotation_count} label{f.annotation_count !== 1 ? "s" : ""}
                          </span>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Stats row */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                {[
                  { label: "Annotations", value: overview.total_annotations },
                  { label: "Classes", value: overview.classes.length, sub: overview.classes.join(", ") },
                  { label: "Review Progress", value: `${reviewProgress}%`, progress: reviewProgress },
                  { label: "Corrections", value: overview.feedback_count, sub: "false positives flagged" },
                ].map((stat) => (
                  <div
                    key={stat.label}
                    className="rounded-lg p-3"
                    style={{ backgroundColor: "var(--bg-elevated)", border: "1px solid var(--border-subtle)" }}
                  >
                    <span className="eyebrow block">{stat.label}</span>
                    <span style={{ fontSize: 18, fontWeight: 700, fontFamily: "var(--font-serif)", color: "var(--text-primary)" }}>
                      {stat.value}
                    </span>
                    {stat.sub && (
                      <span className="block truncate" style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                        {stat.sub}
                      </span>
                    )}
                    {stat.progress != null && (
                      <div className="w-full h-1.5 rounded-full mt-1" style={{ backgroundColor: "var(--bg-inset)" }}>
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${stat.progress}%`, backgroundColor: "var(--success)" }}
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Auto-labeling / add-class progress indicator */}
              {overview.labeling_in_progress > 0 && (
                <div
                  className="rounded-lg px-3 py-2.5 mb-4 text-sm space-y-2"
                  style={{ backgroundColor: "var(--accent-soft)", border: "1px solid var(--accent)", color: "var(--accent)" }}
                >
                  {overview.in_progress_details.length > 0 ? (
                    overview.in_progress_details.map((d) => (
                      <div key={d.class_name}>
                        <div className="flex items-center gap-2">
                          <Loader size={13} className="animate-spin shrink-0" />
                          <span>
                            Adding <strong>{d.class_name}</strong>
                            {d.total > 0 && (
                              <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginLeft: 6 }}>
                                {d.processed}/{d.total} video{d.total !== 1 ? "s" : ""}
                              </span>
                            )}
                            {d.status === "pending" && (
                              <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginLeft: 6, opacity: 0.7 }}>
                                queued
                              </span>
                            )}
                          </span>
                        </div>
                        {d.total > 0 && (
                          <div className="w-full h-1.5 rounded-full mt-1.5" style={{ backgroundColor: "rgba(0,0,0,0.1)" }}>
                            <div
                              className="h-full rounded-full transition-all"
                              style={{ width: `${Math.max(2, d.progress * 100)}%`, backgroundColor: "var(--accent)" }}
                            />
                          </div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="flex items-center gap-2">
                      <Loader size={13} className="animate-spin shrink-0" />
                      <span>
                        <strong>{overview.labeling_in_progress}</strong> video{overview.labeling_in_progress !== 1 ? "s" : ""} being auto-labeled
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* Annotation status breakdown */}
              <div className="flex gap-3 text-xs mb-4" style={{ color: "var(--text-secondary)" }}>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: "var(--success)" }} />
                  {overview.accepted} accepted
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: "var(--danger)" }} />
                  {overview.rejected} rejected
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: "var(--text-muted)" }} />
                  {overview.pending} pending
                </span>
              </div>

              {/* Primary actions */}
              <div className="flex flex-wrap gap-2 mb-3">
                <Link
                  to={`/review/${job.job_id}`}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium"
                  style={{ backgroundColor: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }}
                >
                  <Eye size={14} />
                  {overview.pending > 0 ? `Review (${overview.pending} pending)` : "View Annotations"}
                </Link>
                <Link
                  to={`/train/${job.job_id}`}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium text-white"
                  style={{ backgroundColor: "var(--accent)" }}
                >
                  <Cpu size={14} />
                  Train New Model
                </Link>
                <div className="relative">
                  <button
                    onClick={() => setShowExport(!showExport)}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm"
                    style={{ backgroundColor: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}
                  >
                    <Download size={14} />
                    Export
                    <ChevronDown size={12} style={{ opacity: 0.5 }} />
                  </button>
                  {showExport && (
                    <div
                      className="absolute top-full left-0 mt-1 rounded-lg py-1 z-20"
                      style={{ backgroundColor: "var(--bg-elevated)", border: "1px solid var(--border-default)", boxShadow: "var(--shadow-md)", minWidth: 180 }}
                    >
                      <p className="eyebrow px-3 py-1">Export format</p>
                      {overview.dataset_url && (
                        <a
                          href={overview.dataset_url}
                          onClick={() => setShowExport(false)}
                          className="flex items-center gap-2 px-3 py-2 text-sm w-full text-left"
                          style={{ color: "var(--text-primary)" }}
                          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-surface-hover)")}
                          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}
                        >
                          <Download size={13} style={{ color: "var(--text-muted)" }} />
                          <div>
                            <span className="font-medium">Original</span>
                            <span className="block" style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                              Pre-built dataset zip
                            </span>
                          </div>
                        </a>
                      )}
                      {[
                        { fmt: "segment", label: "YOLO Segment", desc: "Polygon labels" },
                        { fmt: "detect", label: "YOLO Detect", desc: "Bounding box labels" },
                        { fmt: "obb", label: "YOLO OBB", desc: "Oriented bounding boxes" },
                      ].map(({ fmt, label, desc }) => (
                        <button
                          key={fmt}
                          disabled={exporting}
                          onClick={async () => {
                            setExporting(true);
                            setShowExport(false);
                            try {
                              const result = await exportDataset(job.job_id, fmt);
                              window.open(result.download_url, "_blank");
                            } catch (e: any) {
                              setUploadMsg(`Error: ${e.message}`);
                            } finally {
                              setExporting(false);
                            }
                          }}
                          className="flex items-center gap-2 px-3 py-2 text-sm w-full text-left disabled:opacity-50"
                          style={{ color: "var(--text-primary)" }}
                          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "var(--bg-surface-hover)")}
                          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "")}
                        >
                          <Download size={13} style={{ color: "var(--text-muted)" }} />
                          <div>
                            <span className="font-medium">{label}</span>
                            <span className="block" style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                              {desc}
                            </span>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  onClick={handleDelete}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm ml-auto"
                  style={{ color: "var(--danger)", border: "1px solid var(--border-default)" }}
                >
                  <Trash2 size={14} />
                  Delete
                </button>
              </div>

              {/* Add data */}
              <div className="pt-3" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                <p className="eyebrow mb-2">
                  Add data to "{collectionName}"
                  {uploading && <span className="ml-2" style={{ color: "var(--accent)" }}>uploading...</span>}
                </p>
                <div className="flex flex-wrap gap-2 mb-2">
                  {[
                    { onClick: () => imageInputRef.current?.click(), icon: <Plus size={14} />, label: "Add Images" },
                    { onClick: () => videoInputRef.current?.click(), icon: <UploadIcon size={14} />, label: "Add Videos" },
                    { onClick: () => setShowImport(!showImport), icon: <FolderInput size={14} />, label: "Import from Collection" },
                  ].map((btn) => (
                    <button
                      key={btn.label}
                      onClick={btn.onClick}
                      disabled={uploading}
                      className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm disabled:opacity-50"
                      style={{
                        backgroundColor: "var(--bg-elevated)",
                        border: "1px dashed var(--border-default)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {btn.icon}
                      {btn.label}
                    </button>
                  ))}
                  <input
                    ref={imageInputRef}
                    type="file"
                    accept="image/*"
                    multiple
                    className="hidden"
                    onChange={(e) => { handleImageUpload(e.target.files); e.target.value = ""; }}
                  />
                  <input
                    ref={videoInputRef}
                    type="file"
                    accept="video/*"
                    multiple
                    className="hidden"
                    onChange={(e) => { handleVideoUpload(e.target.files); e.target.value = ""; }}
                  />
                </div>
                {/* Import from collection picker */}
                {showImport && allProjects && (
                  <div className="rounded-lg p-3 mb-2" style={{ backgroundColor: "var(--bg-elevated)", border: "1px solid var(--border-default)" }}>
                    <p className="eyebrow mb-2">Select a collection to import videos from</p>
                    <div className="space-y-1 max-h-40 overflow-y-auto">
                      {allProjects.filter((p) => p.name !== collectionName).map((p) => (
                        <button
                          key={p.id}
                          onClick={() => setImportingFrom(p.id)}
                          className="w-full text-left px-3 py-2 rounded-lg text-sm transition-colors"
                          style={{
                            backgroundColor: importingFrom === p.id ? "var(--accent-soft)" : undefined,
                            border: importingFrom === p.id ? "1px solid var(--accent)" : "1px solid transparent",
                          }}
                        >
                          <span className="font-medium" style={{ color: "var(--text-primary)" }}>{p.name}</span>
                          <span className="ml-2" style={{ color: "var(--text-muted)" }}>{p.video_count} video{p.video_count !== 1 ? "s" : ""}</span>
                        </button>
                      ))}
                    </div>
                    {importingFrom && importVideos && (
                      <div className="mt-2 pt-2 flex items-center justify-between" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                          {importVideos.length} video{importVideos.length !== 1 ? "s" : ""} will be linked and auto-labeled
                        </span>
                        <button
                          onClick={handleImportAll}
                          disabled={uploading}
                          className="px-3 py-1.5 text-xs rounded-lg text-white disabled:opacity-50"
                          style={{ backgroundColor: "var(--accent)" }}
                        >
                          Import All
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {uploadMsg && (
                  <p className="text-xs mb-2" style={{ color: uploadMsg.startsWith("Error") ? "var(--danger)" : "var(--success)" }}>
                    {uploadMsg}
                  </p>
                )}
                {overview.feedback_count > 0 && (
                  <div>
                    <button
                      onClick={() => setShowCorrections(!showCorrections)}
                      className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm transition-colors w-full text-left"
                      style={{ backgroundColor: "var(--warning-soft)", border: "1px solid var(--warning)", color: "var(--warning)" }}
                    >
                      <MessageSquareWarning size={14} />
                      <span>
                        <strong>{overview.feedback_count}</strong> false positive{overview.feedback_count !== 1 ? "s" : ""}
                      </span>
                      <span className="text-xs ml-auto" style={{ opacity: 0.7 }}>{showCorrections ? "Hide" : "Review"}</span>
                    </button>
                    {showCorrections && feedback && (
                      <div className="mt-2">
                        {/* Visual grid for feedback with frame images */}
                        {feedback.some((fb) => fb.frame_url) && (
                          <div className="grid grid-cols-3 sm:grid-cols-4 gap-2 mb-2">
                            {feedback.filter((fb) => fb.frame_url).map((fb) => (
                              <div
                                key={fb.id}
                                className="relative rounded-lg overflow-hidden"
                                style={{ border: "2px solid var(--danger)" }}
                              >
                                <img
                                  src={fb.frame_url!}
                                  alt={`False positive: ${fb.class_name}`}
                                  className="w-full aspect-video object-cover"
                                  loading="lazy"
                                />
                                {/* Reject overlay */}
                                <div className="absolute inset-0" style={{ backgroundColor: "rgba(220, 38, 38, 0.15)" }} />
                                <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ opacity: 0.3 }}>
                                  <X size={48} style={{ color: "var(--danger)" }} strokeWidth={3} />
                                </div>
                                {/* Label pill */}
                                <div className="absolute bottom-0 left-0 right-0 px-2 py-1.5" style={{ background: "linear-gradient(transparent, rgba(0,0,0,0.7))" }}>
                                  <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "#fff" }}>
                                    {fb.class_name}
                                    {fb.confidence != null && (
                                      <span style={{ marginLeft: 4, opacity: 0.7 }}>{(fb.confidence * 100).toFixed(0)}%</span>
                                    )}
                                  </span>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                        {/* Text list for feedback without images */}
                        <div
                          className="rounded-lg overflow-hidden"
                          style={{ backgroundColor: "var(--bg-elevated)", border: "1px solid var(--border-default)" }}
                        >
                          {feedback.filter((fb) => !fb.frame_url).map((fb, i) => (
                            <div
                              key={fb.id}
                              className="px-3 py-2 flex items-center justify-between text-xs"
                              style={{ borderTop: i > 0 ? "1px solid var(--border-subtle)" : undefined }}
                            >
                              <div className="flex items-center gap-2">
                                <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: "var(--danger)" }} />
                                <span className="font-medium" style={{ color: "var(--text-primary)" }}>{fb.class_name}</span>
                                {fb.confidence != null && (
                                  <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{(fb.confidence * 100).toFixed(0)}%</span>
                                )}
                                {fb.frame_index != null && (
                                  <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>frame {fb.frame_index}</span>
                                )}
                              </div>
                              <span className="font-medium" style={{ color: "var(--danger)", fontFamily: "var(--font-mono)", fontSize: 10 }}>false positive</span>
                            </div>
                          ))}
                          {feedback.filter((fb) => !fb.frame_url).length === 0 && feedback.length > 0 && (
                            <p className="px-3 py-2 text-xs" style={{ color: "var(--text-muted)" }}>
                              All false positives have frame previews above.
                            </p>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Class management — always shown so users can recover from deleting every class */}
              <Accordion title="Manage Classes" eyebrow="Class operations" count={overview.classes.length} className="mt-3">
                {overview.classes.length === 0 && (
                  <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
                    No classes yet. Add one to auto-label this dataset's videos with a new prompt.
                  </p>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {overview.classes.map((cls) => (
                      <div key={cls} className="flex items-center gap-1.5 surface" style={{ padding: "4px 10px", borderRadius: 10 }}>
                        <Tag size={10} style={{ color: "var(--text-muted)" }} />
                        <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{cls}</span>
                        <button
                          onClick={async () => {
                            const target = prompt(`Rename "${cls}" to:`, cls);
                            if (target && target !== cls) {
                              await mergeClasses(job.job_id, cls, target);
                              queryClient.invalidateQueries({ queryKey: ["dataset-overview", job.job_id] });
                            }
                          }}
                          style={{ fontSize: 9, color: "var(--accent)", background: "none", border: "none", marginLeft: 4 }}
                          title="Rename/merge class"
                        >
                          <Merge size={10} />
                        </button>
                        <button
                          onClick={async () => {
                            if (confirm(`Delete all "${cls}" annotations?`)) {
                              await deleteClass(job.job_id, cls);
                              queryClient.invalidateQueries({ queryKey: ["dataset-overview", job.job_id] });
                            }
                          }}
                          style={{ fontSize: 9, color: "var(--danger)", background: "none", border: "none" }}
                          title="Delete class"
                        >
                          <Trash2 size={10} />
                        </button>
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-2 mt-3">
                    <button
                      onClick={async () => {
                        const name = prompt("New class name:");
                        if (!name) return;
                        const promptText = prompt(`Detection prompt(s) for "${name}" (comma-separated for aliases):`, name);
                        if (!promptText) return;
                        try {
                          await addClassToDataset(job.job_id, name, promptText);
                          setUploadMsg(`Labeling "${name}" — results will merge into this dataset`);
                          queryClient.invalidateQueries({ queryKey: ["dataset-overview", job.job_id] });
                        } catch (e: any) {
                          setUploadMsg(`Error: ${e.message}`);
                        }
                      }}
                      className="flex items-center gap-1 text-xs px-3 py-1.5 surface"
                      style={{ color: "var(--accent)", borderRadius: 8 }}
                    >
                      <Plus size={11} /> Add Class
                    </button>
                    <button
                      onClick={async () => {
                        const result = await duplicateDataset(job.job_id);
                        queryClient.invalidateQueries({ queryKey: ["jobs"] });
                        alert(`Dataset duplicated. ${result.annotations_copied} annotations copied.`);
                      }}
                      className="flex items-center gap-1 text-xs px-3 py-1.5 surface"
                      style={{ color: "var(--text-secondary)", borderRadius: 8 }}
                    >
                      <Copy size={11} /> Duplicate Dataset
                    </button>
                  </div>
              </Accordion>
            </>
          )}
        </div>
      )}

      {/* Frame gallery flyout */}
      {galleryIdx != null && overview && (() => {
        const frame = overview.sample_frames[galleryIdx];
        if (!frame || !frame.thumbnail_url) return null;
        return (
          <DatasetAnnotationViewer
            frameId={frame.frame_id}
            imageUrl={frame.thumbnail_url}
            jobId={job.job_id}
            classes={overview.classes}
            onClose={() => setGalleryIdx(null)}
            onPrev={galleryIdx > 0 ? () => setGalleryIdx(galleryIdx - 1) : undefined}
            onNext={galleryIdx < overview.sample_frames.length - 1 ? () => setGalleryIdx(galleryIdx + 1) : undefined}
          />
        );
      })()}
    </div>
  );
}

export default function DatasetsPage() {
  const queryClient = useQueryClient();

  const { data: jobs, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => listJobs(),
  });

  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<"newest" | "name" | "labels" | "videos">("newest");
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  // Only show meaningful datasets (multi-frame or with real prompts)
  const allDatasets = jobs
    ?.filter((j) => j.status === "completed" && j.total_frames > 0)
    .reverse() || [];

  // By default, only show substantial datasets (not 1-frame test jobs)
  const substantial = showAll
    ? allDatasets
    : allDatasets.filter((j) => j.total_frames > 5);

  // Filter by search term (matches name or prompt)
  const needle = search.toLowerCase().trim();
  const searched = needle
    ? substantial.filter((j) =>
        (j.name || "").toLowerCase().includes(needle) ||
        (j.text_prompt || "").toLowerCase().includes(needle)
      )
    : substantial;

  // Sort
  const datasets = [...searched].sort((a, b) => {
    if (sortBy === "name") return (a.name || a.text_prompt || "").localeCompare(b.name || b.text_prompt || "");
    if (sortBy === "labels") return (b.annotation_count || 0) - (a.annotation_count || 0);
    if (sortBy === "videos") return b.total_frames - a.total_frames;
    return 0; // newest — already in reverse chronological order
  });

  return (
    <div className="min-h-screen">

      <div className="max-w-4xl mx-auto mt-6 px-4 pb-12">
        <div className="flex items-center justify-between mb-6">
          <div>
            <p className="eyebrow" style={{ marginBottom: 4 }}>Data management</p>
            <h1 className="text-2xl font-bold flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
              <Database size={24} />
              Datasets
            </h1>
            <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
              Your labeled datasets. Click to browse, review, or train.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {allDatasets.length > 1 && (
              <button
                onClick={() => { setSelecting(!selecting); setSelected(new Set()); }}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm"
                style={{
                  backgroundColor: selecting ? "var(--accent-soft)" : "var(--bg-inset)",
                  border: selecting ? "1px solid var(--accent)" : "1px solid var(--border-subtle)",
                  color: selecting ? "var(--accent)" : "var(--text-secondary)",
                }}
              >
                <SquareCheck size={14} />
                {selecting ? "Cancel" : "Select"}
              </button>
            )}
            <Link
              to="/upload"
              className="px-4 py-2 text-white rounded-lg text-sm"
              style={{ backgroundColor: "var(--accent)" }}
            >
              + New Dataset
            </Link>
          </div>
        </div>

        {/* Search + sort bar */}
        {allDatasets.length > 0 && (
          <div className="flex gap-2 mb-4">
            <div className="relative flex-1">
              <Search
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
                style={{ color: "var(--text-muted)" }}
              />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search datasets..."
                className="w-full pl-9 pr-3 py-2 rounded-lg text-sm"
                style={{
                  backgroundColor: "var(--bg-inset)",
                  border: "1px solid var(--border-subtle)",
                  color: "var(--text-primary)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 13,
                  outline: "none",
                }}
                onFocus={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
                onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border-subtle)")}
              />
            </div>
            <div className="flex items-center gap-1.5 px-3 rounded-lg shrink-0" style={{ backgroundColor: "var(--bg-inset)", border: "1px solid var(--border-subtle)" }}>
              <ArrowUpDown size={12} style={{ color: "var(--text-muted)" }} />
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
                style={{
                  background: "none",
                  border: "none",
                  color: "var(--text-secondary)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  outline: "none",
                  cursor: "pointer",
                }}
              >
                <option value="newest">Newest</option>
                <option value="name">Name</option>
                <option value="labels">Most labels</option>
                <option value="videos">Most videos</option>
              </select>
            </div>
          </div>
        )}

        {isLoading && <p style={{ color: "var(--text-muted)" }}>Loading datasets...</p>}

        {!showAll && allDatasets.length > datasets.length && (
          <div className="flex items-center gap-2 mb-4 text-xs" style={{ color: "var(--text-muted)" }}>
            Showing {datasets.length} of {allDatasets.length} datasets.
            <button onClick={() => setShowAll(true)} className="hover:underline" style={{ color: "var(--accent)" }}>
              Show all
            </button>
          </div>
        )}

        {/* Select all toggle */}
        {selecting && datasets.length > 0 && (
          <div className="flex items-center gap-2 mb-2">
            <button
              onClick={() => {
                if (selected.size === datasets.length) {
                  setSelected(new Set());
                } else {
                  setSelected(new Set(datasets.map((j) => j.job_id)));
                }
              }}
              className="text-xs hover:underline"
              style={{ color: "var(--accent)", fontFamily: "var(--font-mono)" }}
            >
              {selected.size === datasets.length ? "Deselect all" : `Select all (${datasets.length})`}
            </button>
            {selected.size > 0 && (
              <span className="text-xs" style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                {selected.size} selected
              </span>
            )}
          </div>
        )}

        <div className="space-y-3">
          {datasets.map((job) => (
            <DatasetCard
              key={job.job_id}
              job={job}
              onDeleted={() => { queryClient.invalidateQueries({ queryKey: ["jobs"] }); queryClient.refetchQueries({ queryKey: ["jobs"] }); }}
              selected={selecting ? selected.has(job.job_id) : undefined}
              onToggleSelect={selecting ? () => {
                setSelected((prev) => {
                  const next = new Set(prev);
                  if (next.has(job.job_id)) next.delete(job.job_id);
                  else next.add(job.job_id);
                  return next;
                });
              } : undefined}
            />
          ))}
        </div>

        {!isLoading && datasets.length === 0 && (
          <div className="text-center py-16">
            <Database size={48} className="mx-auto mb-4" style={{ color: "var(--text-muted)" }} />
            {needle ? (
              <>
                <h2 className="text-lg font-medium mb-2" style={{ color: "var(--text-primary)" }}>No matches</h2>
                <p className="mb-6 max-w-md mx-auto" style={{ color: "var(--text-secondary)" }}>
                  No datasets match "{search}".
                </p>
              </>
            ) : (
              <>
                <h2 className="text-lg font-medium mb-2" style={{ color: "var(--text-primary)" }}>No datasets yet</h2>
                <p className="mb-6 max-w-md mx-auto" style={{ color: "var(--text-secondary)" }}>
                  Upload a video and label objects to create your first dataset.
                </p>
                <Link
                  to="/upload"
                  className="inline-block px-6 py-2 text-white rounded-lg"
                  style={{ backgroundColor: "var(--accent)" }}
                >
                  Upload Video
                </Link>
              </>
            )}
          </div>
        )}
      </div>

      {/* Floating bulk action bar */}
      {selecting && selected.size > 0 && (
        <div
          className="fixed bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-3 px-5 py-3 rounded-xl z-50"
          style={{
            backgroundColor: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          <span style={{ fontSize: 13, fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
            {selected.size} dataset{selected.size !== 1 ? "s" : ""}
          </span>
          <div style={{ width: 1, height: 20, backgroundColor: "var(--border-default)" }} />
          <button
            disabled={bulkBusy}
            onClick={async () => {
              if (!confirm(`Delete ${selected.size} dataset${selected.size !== 1 ? "s" : ""} and all their annotations? This cannot be undone.`)) return;
              setBulkBusy(true);
              try {
                await Promise.all(Array.from(selected).map((id) => deleteJob(id)));
                setSelected(new Set());
                setSelecting(false);
                queryClient.invalidateQueries({ queryKey: ["jobs"] });
              } catch (e: any) {
                alert(`Error: ${e.message}`);
              } finally {
                setBulkBusy(false);
              }
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm disabled:opacity-50"
            style={{ color: "var(--danger)", border: "1px solid var(--danger)" }}
          >
            <Trash2 size={13} />
            Delete
          </button>
          <button
            disabled={bulkBusy}
            onClick={async () => {
              setBulkBusy(true);
              try {
                for (const id of selected) {
                  const result = await exportDataset(id, "segment");
                  window.open(result.download_url, "_blank");
                }
              } catch (e: any) {
                alert(`Error: ${e.message}`);
              } finally {
                setBulkBusy(false);
              }
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm disabled:opacity-50"
            style={{ backgroundColor: "var(--accent)", color: "#fff" }}
          >
            <Download size={13} />
            Export All
          </button>
        </div>
      )}
    </div>
  );
}
