import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { listJobs, getDatasetOverview, uploadImages, uploadVideo, listFeedback, listProjects, listProjectVideos, linkVideos, deleteJob, listAnnotations, updateAnnotation, type JobStatus } from "../api";
import AnnotationCanvas from "../components/AnnotationCanvas";
import { Database, CheckCircle, Clock, AlertCircle, Download, Eye, Cpu, MessageSquareWarning, Images, Plus, Upload as UploadIcon, Loader, FolderInput, Trash2 } from "lucide-react";

function DatasetAnnotationViewer({ frameId, imageUrl, jobId, classes, onClose, onPrev, onNext }: {
  frameId: string; imageUrl: string; jobId: string; classes: string[];
  onClose: () => void; onPrev?: () => void; onNext?: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: allAnnotations } = useQuery({
    queryKey: ["annotations", jobId],
    queryFn: () => listAnnotations(jobId),
    staleTime: 30000,
  });
  const frameAnnotations = allAnnotations?.filter((a) => a.frame_id === frameId) || [];

  return (
    <AnnotationCanvas
      imageUrl={imageUrl}
      frameId={frameId}
      jobId={jobId}
      annotations={frameAnnotations}
      classes={classes}
      onAccept={(id) => { updateAnnotation(id, { status: "accepted" }); queryClient.invalidateQueries({ queryKey: ["annotations", jobId] }); }}
      onReject={(id) => { updateAnnotation(id, { status: "rejected" }); queryClient.invalidateQueries({ queryKey: ["annotations", jobId] }); }}
      onClose={onClose}
      onPrev={onPrev}
      onNext={onNext}
      onAnnotationCreated={() => queryClient.invalidateQueries({ queryKey: ["annotations", jobId] })}
    />
  );
}


function DatasetCard({ job, onDeleted }: { job: JobStatus; onDeleted: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [galleryIdx, setGalleryIdx] = useState<number | null>(null);
  const [showCorrections, setShowCorrections] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importingFrom, setImportingFrom] = useState<string | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);

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

  const collectionName = job.text_prompt || "default";

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
    try {
      for (const file of Array.from(files)) {
        try {
          await uploadVideo(file, collectionName);
          added++;
          setUploadMsg(`Uploading... ${added}/${files.length} videos`);
        } catch (e: any) {
          if (e.message?.includes("duplicate") || e.message?.includes("already exists")) {
            skipped++;
          } else {
            throw e;
          }
        }
      }
      const parts = [`Added ${added} video${added !== 1 ? "s" : ""} to "${collectionName}"`];
      if (skipped > 0) parts.push(`(${skipped} duplicate${skipped !== 1 ? "s" : ""} skipped)`);
      if (added > 0) parts.push("— auto-labeling started");
      setUploadMsg(parts.join(" "));
    } catch (e: any) {
      setUploadMsg(`Error: ${e.message}`);
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

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      {/* Card header */}
      <div
        className="p-4 cursor-pointer hover:bg-gray-50/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-4">
          {/* Status indicator */}
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
            job.status === "completed" ? "bg-green-100" : job.status === "failed" ? "bg-red-100" : "bg-blue-100"
          }`}>
            {job.status === "completed" ? <CheckCircle size={20} className="text-green-600" />
              : job.status === "failed" ? <AlertCircle size={20} className="text-red-500" />
                : <Clock size={20} className="text-blue-500" />}
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-gray-900">{job.text_prompt || "Exemplar labeling"}</h3>
            <p className="text-xs text-gray-400 mt-0.5">
              {job.total_frames} frames &middot; {job.processed_frames} processed
            </p>
          </div>

          {/* Quick stats */}
          <div className="flex gap-6 text-xs shrink-0">
            <div className="text-center">
              <span className="text-gray-400 block">Status</span>
              <span className={`font-medium capitalize ${
                job.status === "completed" ? "text-green-600" : job.status === "failed" ? "text-red-500" : "text-blue-600"
              }`}>{job.status}</span>
            </div>
            <div className="text-center">
              <span className="text-gray-400 block">Frames</span>
              <span className="font-medium">{job.total_frames}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-gray-100 p-4 bg-gray-50/30">
          {!overview ? (
            <p className="text-sm text-gray-400 py-4 text-center">Loading dataset...</p>
          ) : (
            <>
              {/* Thumbnail grid */}
              {overview.sample_frames.length > 0 && (
                <div className="mb-4">
                  <p className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1">
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
                            className="w-full aspect-video object-cover rounded-lg border border-gray-200 group-hover:border-blue-400 transition-colors" loading="lazy"
                          />
                        ) : (
                          <div className="w-full aspect-video bg-gray-200 rounded-lg" />
                        )}
                        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent rounded-b-lg px-1.5 py-1">
                          <span className="text-[10px] text-white font-medium">
                            {f.annotation_count} label{f.annotation_count !== 1 ? "s" : ""}
                          </span>
                        </div>
                        <div className="absolute inset-0 bg-blue-500/0 group-hover:bg-blue-500/10 rounded-lg transition-colors" />
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Stats row */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                <div className="bg-white rounded-lg border border-gray-200 p-3">
                  <span className="text-xs text-gray-400 block">Annotations</span>
                  <span className="text-lg font-bold">{overview.total_annotations}</span>
                </div>
                <div className="bg-white rounded-lg border border-gray-200 p-3">
                  <span className="text-xs text-gray-400 block">Classes</span>
                  <span className="text-lg font-bold">{overview.classes.length}</span>
                  <span className="text-[10px] text-gray-400 block truncate">{overview.classes.join(", ")}</span>
                </div>
                <div className="bg-white rounded-lg border border-gray-200 p-3">
                  <span className="text-xs text-gray-400 block">Review Progress</span>
                  <span className="text-lg font-bold">{reviewProgress}%</span>
                  <div className="w-full h-1.5 bg-gray-200 rounded-full mt-1">
                    <div
                      className="h-full bg-green-500 rounded-full"
                      style={{ width: `${reviewProgress}%` }}
                    />
                  </div>
                </div>
                <div className="bg-white rounded-lg border border-gray-200 p-3">
                  <span className="text-xs text-gray-400 block">Corrections</span>
                  <span className="text-lg font-bold">{overview.feedback_count}</span>
                  <span className="text-[10px] text-gray-400 block">false positives flagged</span>
                </div>
              </div>

              {/* Auto-labeling indicator */}
              {overview.labeling_in_progress > 0 && (
                <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 mb-4 text-sm text-blue-700">
                  <Loader size={14} className="animate-spin shrink-0" />
                  <span>
                    <strong>{overview.labeling_in_progress}</strong> new video{overview.labeling_in_progress !== 1 ? "s" : ""} being auto-labeled with "{overview.prompt}"
                  </span>
                </div>
              )}

              {/* Annotation status breakdown */}
              <div className="flex gap-3 text-xs mb-4">
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  {overview.accepted} accepted
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-red-500" />
                  {overview.rejected} rejected
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-gray-400" />
                  {overview.pending} pending
                </span>
              </div>

              {/* Primary actions */}
              <div className="flex flex-wrap gap-2 mb-3">
                <Link
                  to={`/review/${job.job_id}`}
                  className="flex items-center gap-1.5 px-3 py-2 bg-white border border-gray-200 rounded-lg text-sm hover:bg-gray-50 font-medium"
                >
                  <Eye size={14} />
                  {overview.pending > 0 ? `Review (${overview.pending} pending)` : "View Annotations"}
                </Link>
                <Link
                  to={`/train/${job.job_id}`}
                  className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 font-medium"
                >
                  <Cpu size={14} />
                  Train New Model
                </Link>
                {overview.dataset_url && (
                  <a
                    href={overview.dataset_url}
                    className="flex items-center gap-1.5 px-3 py-2 bg-white border border-gray-200 rounded-lg text-sm hover:bg-gray-50"
                  >
                    <Download size={14} />
                    Download
                  </a>
                )}
                <button
                  onClick={handleDelete}
                  className="flex items-center gap-1.5 px-3 py-2 text-red-500 hover:bg-red-50 border border-gray-200 hover:border-red-200 rounded-lg text-sm ml-auto"
                >
                  <Trash2 size={14} />
                  Delete
                </button>
              </div>

              {/* Add data */}
              <div className="pt-3 border-t border-gray-200">
                <p className="text-xs font-medium text-gray-500 mb-2">
                  Add data to "{collectionName}"
                  {uploading && <span className="ml-2 text-blue-600">uploading...</span>}
                </p>
                <div className="flex flex-wrap gap-2 mb-2">
                  <button
                    onClick={() => imageInputRef.current?.click()}
                    disabled={uploading}
                    className="flex items-center gap-1.5 px-3 py-2 bg-white border border-dashed border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-50 hover:border-gray-400 disabled:opacity-50"
                  >
                    <Plus size={14} />
                    Add Images
                  </button>
                  <input
                    ref={imageInputRef}
                    type="file"
                    accept="image/*"
                    multiple
                    className="hidden"
                    onChange={(e) => { handleImageUpload(e.target.files); e.target.value = ""; }}
                  />
                  <button
                    onClick={() => videoInputRef.current?.click()}
                    disabled={uploading}
                    className="flex items-center gap-1.5 px-3 py-2 bg-white border border-dashed border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-50 hover:border-gray-400 disabled:opacity-50"
                  >
                    <UploadIcon size={14} />
                    Add Videos
                  </button>
                  <button
                    onClick={() => setShowImport(!showImport)}
                    disabled={uploading}
                    className="flex items-center gap-1.5 px-3 py-2 bg-white border border-dashed border-gray-300 rounded-lg text-sm text-gray-600 hover:bg-gray-50 hover:border-gray-400 disabled:opacity-50"
                  >
                    <FolderInput size={14} />
                    Import from Collection
                  </button>
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
                  <div className="bg-white border border-gray-200 rounded-lg p-3 mb-2">
                    <p className="text-xs font-medium text-gray-500 mb-2">Select a collection to import videos from:</p>
                    <div className="space-y-1 max-h-40 overflow-y-auto">
                      {allProjects.filter((p) => p.name !== collectionName).map((p) => (
                        <button
                          key={p.id}
                          onClick={() => setImportingFrom(p.id)}
                          className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                            importingFrom === p.id ? "bg-blue-50 border border-blue-200" : "hover:bg-gray-50"
                          }`}
                        >
                          <span className="font-medium">{p.name}</span>
                          <span className="text-gray-400 ml-2">{p.video_count} video{p.video_count !== 1 ? "s" : ""}</span>
                        </button>
                      ))}
                    </div>
                    {importingFrom && importVideos && (
                      <div className="mt-2 pt-2 border-t border-gray-100 flex items-center justify-between">
                        <span className="text-xs text-gray-500">
                          {importVideos.length} video{importVideos.length !== 1 ? "s" : ""} will be linked and auto-labeled
                        </span>
                        <button
                          onClick={handleImportAll}
                          disabled={uploading}
                          className="px-3 py-1.5 bg-blue-600 text-white text-xs rounded-lg hover:bg-blue-700 disabled:opacity-50"
                        >
                          Import All
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {uploadMsg && (
                  <p className={`text-xs mb-2 ${uploadMsg.startsWith("Error") ? "text-red-600" : "text-green-600"}`}>
                    {uploadMsg}
                  </p>
                )}
                {overview.feedback_count > 0 && (
                  <div>
                    <button
                      onClick={() => setShowCorrections(!showCorrections)}
                      className="flex items-center gap-1.5 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm hover:bg-amber-100 transition-colors w-full text-left"
                    >
                      <MessageSquareWarning size={14} className="text-amber-600" />
                      <span className="text-amber-700">
                        <strong>{overview.feedback_count}</strong> false positives → negative examples
                      </span>
                      <span className="text-amber-400 text-xs ml-auto">{showCorrections ? "Hide" : "View"}</span>
                    </button>
                    {showCorrections && feedback && (
                      <div className="mt-2 bg-white border border-gray-200 rounded-lg divide-y divide-gray-100 max-h-60 overflow-y-auto">
                        {feedback.map((fb) => (
                          <div key={fb.id} className="px-3 py-2 flex items-center justify-between text-xs">
                            <div className="flex items-center gap-2">
                              <span className="w-2 h-2 rounded-full bg-red-400 shrink-0" />
                              <span className="font-medium text-gray-700">{fb.class_name}</span>
                              {fb.confidence != null && (
                                <span className="text-gray-400">{(fb.confidence * 100).toFixed(0)}%</span>
                              )}
                              {fb.frame_index != null && (
                                <span className="text-gray-400">frame {fb.frame_index}</span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 text-gray-400">
                              {fb.source_filename && (
                                <span className="truncate max-w-[120px]">{fb.source_filename}</span>
                              )}
                              <span className="text-red-400 font-medium">false positive</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
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

  // Only show meaningful datasets (multi-frame or with real prompts)
  const allDatasets = jobs
    ?.filter((j) => j.status === "completed" && j.total_frames > 0)
    .reverse() || [];

  // By default, only show substantial datasets (not 1-frame test jobs)
  const datasets = showAll
    ? allDatasets
    : allDatasets.filter((j) => j.total_frames > 5);

  return (
    <div className="min-h-screen">

      <div className="max-w-4xl mx-auto mt-6 px-4 pb-12">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
              <Database size={24} />
              Datasets
            </h1>
            <p className="text-gray-500 text-sm mt-1">
              Your labeled datasets. Click to browse frames, review annotations, or start training.
            </p>
          </div>
          <Link
            to="/upload"
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
          >
            + New Dataset
          </Link>
        </div>

        {isLoading && <p className="text-gray-500">Loading datasets...</p>}

        {!showAll && allDatasets.length > datasets.length && (
          <div className="flex items-center gap-2 mb-4 text-xs text-gray-400">
            Showing {datasets.length} of {allDatasets.length} datasets.
            <button onClick={() => setShowAll(true)} className="text-blue-600 hover:underline">
              Show all
            </button>
          </div>
        )}

        <div className="space-y-3">
          {datasets.map((job) => (
            <DatasetCard key={job.job_id} job={job} onDeleted={() => { queryClient.invalidateQueries({ queryKey: ["jobs"] }); queryClient.refetchQueries({ queryKey: ["jobs"] }); }} />
          ))}
        </div>

        {!isLoading && datasets.length === 0 && (
          <div className="text-center py-16">
            <Database size={48} className="mx-auto mb-4 text-gray-300" />
            <h2 className="text-lg font-medium text-gray-900 mb-2">No datasets yet</h2>
            <p className="text-gray-500 mb-6 max-w-md mx-auto">
              Upload a video and label objects to create your first dataset.
            </p>
            <Link
              to="/upload"
              className="inline-block px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              Upload Video
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
