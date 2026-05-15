const BASE = "/api/v1";

/** Get auth headers — injects JWT token from localStorage into all API calls. */
function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("waldo_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Authenticated fetch wrapper. */
export async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const headers = { ...authHeaders(), ...(init?.headers || {}) };
  return fetch(url, { ...init, headers });
}

export interface UploadResult {
  video_id: string;
  project_id: string;
  filename: string;
  minio_key: string;
}

export interface LabelResult {
  job_id: string;
  status: string;
  celery_task_id: string;
}

export interface JobStatus {
  job_id: string;
  name: string | null;
  video_id: string;
  text_prompt: string;
  status: string;
  progress: number;
  total_frames: number;
  processed_frames: number;
  result_url: string | null;
  error_message: string | null;
  celery_task_id: string | null;
  annotation_count: number | null;
  class_count: number | null;
  version: number;
  parent_id: string | null;
}

export interface AnnotationOut {
  id: string;
  frame_id: string;
  class_name: string;
  class_index: number;
  polygon: number[];
  bbox: number[] | null;
  confidence: number | null;
  status: string;
  frame_url: string | null;
}

export interface FrameOut {
  id: string;
  video_id: string;
  frame_number: number;
  timestamp_s: number;
  width: number | null;
  height: number | null;
  image_url: string;
}

export interface JobStats {
  total_annotations: number;
  total_frames: number;
  annotated_frames: number;
  empty_frames: number;
  by_class: { name: string; count: number }[];
  by_status: Record<string, number>;
  annotation_density: number;
}

// ── Upload ──────────────────────────────────────────────────

export async function uploadVideo(file: File, projectName = "default"): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await authFetch(
    `${BASE}/upload?project_name=${encodeURIComponent(projectName)}`,
    { method: "POST", body: form }
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface PreviewDetection {
  bbox: number[];
  score: number;
  label: string;
  polygon: number[] | null;
  track_id: number | null;
}

export interface PreviewFrame {
  frame_idx: number;
  image_b64: string;
  timestamp_s: number;
  width: number;
  height: number;
  detections: PreviewDetection[];
}

export interface PreviewResponse {
  frames: PreviewFrame[];
  total_detections: number;
  unique_track_count: number;
  fps: number;
  video_duration_s: number;
  mode: "sample" | "window";
}

export async function previewPrompts(
  opts: {
    videoId?: string;
    projectId?: string;
    prompts: string[];
    maxFrames?: number;
    threshold?: number;
    startSec?: number;
    durationSec?: number | null;
    sampleFps?: number;
  }
): Promise<PreviewResponse> {
  const res = await authFetch(`${BASE}/label/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_id: opts.videoId || null,
      project_id: opts.projectId || null,
      prompts: opts.prompts,
      max_frames: opts.maxFrames ?? 5,
      threshold: opts.threshold ?? 0.35,
      start_sec: opts.startSec ?? 0.0,
      duration_sec: opts.durationSec ?? null,
      sample_fps: opts.sampleFps ?? 4.0,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface DatasetStatsClass {
  name: string;
  count: number;
  share: number;
}

export interface DatasetStats {
  job_id: string;
  job_name: string | null;
  task_type: string;
  total_frames: number;
  annotated_frames: number;
  empty_frames: number;
  total_annotations: number;
  class_count: number;
  classes: DatasetStatsClass[];
  min_class_count: number;
  max_class_count: number;
  imbalance_ratio: number;
  small_object_ratio: number;
  avg_bbox_area: number;
  recommended_variant: string;
  recommended_epochs: number;
  recommended_batch: number;
  recommended_imgsz: number;
  recommended_augmentation: string;
  warnings: string[];
}

export async function getDatasetStats(jobId: string): Promise<DatasetStats> {
  const res = await authFetch(`${BASE}/train/dataset-stats/${jobId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function startLabeling(
  opts: {
    videoId?: string;
    projectId?: string;
    textPrompt?: string;
    classPrompts?: ClassPrompt[];
    taskType?: string;
  }
): Promise<LabelResult> {
  const res = await authFetch(`${BASE}/label`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_id: opts.videoId || null,
      project_id: opts.projectId || null,
      text_prompt: opts.textPrompt || null,
      class_prompts: opts.classPrompts || null,
      task_type: opts.taskType || "segment",
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function startExemplarLabeling(
  videoId: string,
  frameIdx: number,
  points: number[][],
  labels: number[],
  taskType = "segment",
  className = "object"
): Promise<LabelResult> {
  const res = await authFetch(`${BASE}/label/exemplar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      video_id: videoId,
      frame_idx: frameIdx,
      points,
      labels,
      task_type: taskType,
      class_name: className,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const res = await authFetch(`${BASE}/status/${jobId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listJobs(videoId?: string): Promise<JobStatus[]> {
  const url = videoId ? `${BASE}/status?video_id=${videoId}` : `${BASE}/status`;
  const res = await authFetch(url);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listAnnotations(
  jobId: string,
  status?: string,
  frameId?: string,
  limit?: number,
): Promise<AnnotationOut[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (frameId) params.set("frame_id", frameId);
  if (limit) params.set("limit", String(limit));
  const res = await authFetch(`${BASE}/jobs/${jobId}/annotations?${params}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateAnnotation(
  annotationId: string,
  update: Partial<{ status: string; polygon: number[]; bbox: number[]; class_name: string }>
): Promise<AnnotationOut> {
  const res = await authFetch(`${BASE}/annotations/${annotationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface DatasetOverview {
  job_id: string;
  name: string | null;
  prompt: string;
  status: string;
  total_frames: number;
  labeled_frames: number;
  total_annotations: number;
  accepted: number;
  rejected: number;
  pending: number;
  classes: string[];
  sample_frames: {
    frame_id: string;
    frame_number: number;
    annotation_count: number;
    accepted: number;
    rejected: number;
    pending: number;
    thumbnail_url: string | null;
    classes: string[];
  }[];
  dataset_url: string | null;
  feedback_count: number;
  labeling_in_progress: number;
  in_progress_classes: string[];
  in_progress_details: { class_name: string; status: string; processed: number; total: number; progress: number }[];
}

export async function getDatasetOverview(jobId: string): Promise<DatasetOverview> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/overview`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function duplicateDataset(jobId: string): Promise<{ new_id: string; annotations_copied: number }> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/duplicate`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listJobClasses(jobId: string): Promise<{ classes: { name: string; count: number }[] }> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/classes`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteClass(jobId: string, className: string): Promise<{ deleted_count: number }> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/classes/${encodeURIComponent(className)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function mergeClasses(jobId: string, sourceClass: string, targetClass: string): Promise<{ updated: number }> {
  const res = await authFetch(`${BASE}/annotations/merge-classes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, source_class: sourceClass, target_class: targetClass }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function renameJob(jobId: string, name: string): Promise<{ status: string; name: string | null }> {
  const res = await authFetch(`${BASE}/jobs/${jobId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function addClassToDataset(jobId: string, className: string, prompt?: string): Promise<{ status: string; class_name: string }> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/add-class`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ class_name: className, prompt: prompt || className }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function exportDataset(jobId: string, format: string): Promise<{ download_url: string }> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteJob(jobId: string): Promise<{ status: string; annotations_deleted: number }> {
  const res = await authFetch(`${BASE}/jobs/${jobId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getJobStats(jobId: string): Promise<JobStats> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/stats`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listFrames(videoId: string): Promise<FrameOut[]> {
  const res = await authFetch(`${BASE}/videos/${videoId}/frames`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Collections

export interface ProjectOut {
  id: string;
  name: string;
  video_count: number;
  created_at: string;
}

export interface VideoOut {
  id: string;
  filename: string;
  fps: number | null;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  frame_count: number | null;
  created_at: string;
  url: string | null;
}

export interface ClassPrompt {
  name: string;
  prompt?: string;
  prompts?: string[];
}

export async function uploadVideoBatch(
  files: File[],
  projectName = "default"
): Promise<{ videos: UploadResult[]; project_id: string }> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const res = await authFetch(
    `${BASE}/upload/batch?project_name=${encodeURIComponent(projectName)}`,
    { method: "POST", body: form }
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function uploadImages(
  files: File[],
  projectName?: string
): Promise<{ frame_ids: string[]; urls: string[]; project_id: string }> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  if (projectName) form.append("project_name", projectName);
  const res = await authFetch(`${BASE}/upload/images`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function linkVideos(
  videoIds: string[],
  targetProjectName: string
): Promise<{ linked: number; auto_labeled: number }> {
  const res = await authFetch(`${BASE}/link-videos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_ids: videoIds, target_project_name: targetProjectName }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listProjects(): Promise<ProjectOut[]> {
  const res = await authFetch(`${BASE}/projects`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listProjectVideos(projectId: string): Promise<VideoOut[]> {
  const res = await authFetch(`${BASE}/projects/${projectId}/videos`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Training

export interface TrainingRunStatus {
  run_id: string;
  job_id: string | null;
  name: string;
  task_type: string;
  model_variant: string;
  status: string;
  epoch_current: number;
  total_epochs: number;
  metrics: Record<string, number>;
  best_metrics: Record<string, number>;
  hyperparameters: Record<string, unknown>;
  loss_history: Record<string, number>[];
  metric_history: Record<string, number>[];
  weights_url: string | null;
  error_message: string | null;
  celery_task_id: string | null;
  tags: string[];
  notes: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ModelOut {
  id: string;
  name: string;
  task_type: string;
  model_variant: string;
  version: number;
  metrics: Record<string, number>;
  export_formats: Record<string, string>;
  weights_url: string | null;
  is_active: boolean;
  alias: string | null;
}

export async function startTraining(
  jobId: string,
  opts: { name?: string; model_variant?: string; task_type?: string; hyperparameters?: Record<string, unknown> } = {}
): Promise<{ run_id: string; status: string; celery_task_id: string }> {
  const res = await authFetch(`${BASE}/train`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, ...opts }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateTrainingRun(
  runId: string,
  update: { tags?: string[]; notes?: string }
): Promise<{ status: string }> {
  const res = await authFetch(`${BASE}/train/${runId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteTrainingRun(runId: string): Promise<{ status: string }> {
  const res = await authFetch(`${BASE}/train/${runId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function stopTraining(runId: string): Promise<{ status: string; run_id: string }> {
  const res = await authFetch(`${BASE}/train/${runId}/stop`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getTrainingStatus(runId: string): Promise<TrainingRunStatus> {
  const res = await authFetch(`${BASE}/train/${runId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listTrainingRuns(): Promise<TrainingRunStatus[]> {
  const res = await authFetch(`${BASE}/train`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listModels(): Promise<ModelOut[]> {
  const res = await authFetch(`${BASE}/models`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getVariants(): Promise<{
  variants: Record<string, string>;
  defaults: Record<string, string>;
  hyperparams: Record<string, unknown>;
}> {
  const res = await authFetch(`${BASE}/train/variants`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function exportModel(modelId: string, format: string): Promise<{ task_id: string }> {
  const res = await authFetch(`${BASE}/models/${modelId}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Serve / Inference

export interface DetectionOut {
  class_name: string;
  class_index: number;
  confidence: number;
  bbox: number[];
  track_id: number | null;
  mask: number[][] | null;
}

export interface ImagePredictionResponse {
  detections: DetectionOut[];
  model_id: string | null;
  count: number;
}

export interface FrameResultOut {
  frame_index: number;
  timestamp_s: number;
  detections: DetectionOut[];
}

export interface VideoPredictionResponse {
  frames: FrameResultOut[];
  total_frames: number;
  model_id: string | null;
}

export interface ServeStatus {
  loaded: boolean;
  model_id: string | null;
  model_name: string | null;
  task_type: string | null;
  model_variant: string | null;
  device: string;
  class_names: string[] | null;
}

export async function predictImage(file: File, conf = 0.25, classes?: string[], modelId?: string): Promise<ImagePredictionResponse> {
  const form = new FormData();
  form.append("file", file);
  let url = `${BASE}/predict/image?conf=${conf}`;
  if (classes && classes.length > 0) url += `&classes=${encodeURIComponent(classes.join(","))}`;
  if (modelId) url += `&model_id=${encodeURIComponent(modelId)}`;
  const res = await authFetch(url, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function predictSam(file: File, prompts: string[], conf = 0.15): Promise<ImagePredictionResponse> {
  const form = new FormData();
  form.append("file", file);
  const url = `${BASE}/predict/sam?prompts=${encodeURIComponent(prompts.join(","))}&conf=${conf}`;
  const res = await authFetch(url, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function predictSamVideo(file: File, prompts: string[], conf = 0.35): Promise<VideoPredictionResponse> {
  const form = new FormData();
  form.append("file", file);
  const url = `${BASE}/predict/sam/video?prompts=${encodeURIComponent(prompts.join(","))}&conf=${conf}`;
  const res = await authFetch(url, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface AsyncVideoResult {
  session_id: string;
  celery_task_id: string;
  frame_count: number;
}

export async function predictVideo(
  file: File,
  conf = 0.25,
  classes?: string[],
  modelId?: string,
): Promise<VideoPredictionResponse | AsyncVideoResult> {
  const form = new FormData();
  form.append("file", file);
  let url = `${BASE}/predict/video?conf=${conf}`;
  if (classes && classes.length > 0) url += `&classes=${encodeURIComponent(classes.join(","))}`;
  if (modelId) url += `&model_id=${encodeURIComponent(modelId)}`;
  const res = await authFetch(url, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function streamPredictFrames(
  sessionId: string,
  onFrame: (frame: FrameResultOut) => void,
  onComplete: (totalFrames: number) => void,
  onError: (err: string) => void,
): () => void {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws/predict/${sessionId}`);

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.status === "completed") {
      onComplete(data.total_frames);
      ws.close();
    } else if (data.status === "failed") {
      onError(data.error || "Video processing failed");
      ws.close();
    } else {
      onFrame({
        frame_index: data.frame_index,
        timestamp_s: data.timestamp_s,
        detections: data.detections,
      });
    }
  };

  ws.onerror = () => {
    onError("WebSocket connection failed");
  };

  // Return cleanup function
  return () => {
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  };
}

export async function activateModel(modelId: string): Promise<{ status: string; model_id: string; name: string }> {
  const res = await authFetch(`${BASE}/models/${modelId}/activate`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getServeStatus(): Promise<ServeStatus> {
  const res = await authFetch(`${BASE}/serve/status`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// AI Agent Insights

// Agent — the chat surface lives on /agent/chat (POST) and /agent/stream
// (POST → SSE). See AgentPage.tsx and AgentPanel.tsx; no helper here on
// purpose, the streaming reader needs raw fetch().

// Feedback

export interface FeedbackIn {
  model_id?: string;
  class_name: string;
  bbox: number[];
  polygon?: number[][] | null;
  confidence?: number;
  track_id?: number | null;
  frame_index?: number;
  timestamp_s?: number;
  feedback_type: string;
  corrected_class?: string;
  source_filename?: string;
  frame_image_b64?: string;
}

export interface FeedbackOut {
  id: string;
  feedback_type: string;
  class_name: string;
  confidence: number | null;
  bbox: number[] | null;
  polygon: number[] | null;
  track_id: number | null;
  frame_index: number | null;
  source_filename: string | null;
  frame_url: string | null;
  created_at: string;
}

export async function submitFeedback(item: FeedbackIn): Promise<FeedbackOut> {
  const res = await authFetch(`${BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Interactive SAM3 segmentation

export interface SegmentPointsResponse {
  polygons: number[][];
  bboxes: number[][];
  scores: number[];
}

export async function segmentPoints(
  frameId: string,
  points: number[][],
  labels: number[],
  threshold = 0.3
): Promise<SegmentPointsResponse> {
  const res = await authFetch(`${BASE}/label/segment-points`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ frame_id: frameId, points, labels, threshold }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createAnnotation(data: {
  frame_id: string;
  job_id: string;
  class_name: string;
  class_index?: number;
  polygon: number[];
  bbox?: number[];
  confidence?: number;
  status?: string;
}): Promise<{ id: string; class_name: string; status: string }> {
  const res = await authFetch(`${BASE}/annotations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Feedback

export async function listFeedback(limit = 100): Promise<FeedbackOut[]> {
  const res = await authFetch(`${BASE}/feedback?limit=${limit}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function submitFeedbackBatch(items: FeedbackIn[]): Promise<FeedbackOut[]> {
  const res = await authFetch(`${BASE}/feedback/batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}


// ── Deployment Targets ──────────────────────────────────────────

export interface DeploymentTarget {
  id: string;
  name: string;
  location_label: string | null;
  target_type: string;
  model_id: string | null;
  model_name: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
}

export async function listTargets(): Promise<DeploymentTarget[]> {
  const res = await authFetch(`${BASE}/targets`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createTarget(data: {
  name: string;
  location_label?: string;
  target_type?: string;
  model_id?: string;
  config?: Record<string, unknown>;
}): Promise<DeploymentTarget> {
  const res = await authFetch(`${BASE}/targets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateTarget(
  targetId: string,
  data: Partial<{ name: string; location_label: string; target_type: string; model_id: string; config: Record<string, unknown>; is_active: boolean }>
): Promise<{ status: string }> {
  const res = await authFetch(`${BASE}/targets/${targetId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteTarget(targetId: string): Promise<{ status: string }> {
  const res = await authFetch(`${BASE}/targets/${targetId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}


// ── Inference Metrics ───────────────────────────────────────────

export interface MetricsSummary {
  window: string;
  summary: {
    total_requests: number;
    avg_latency_ms: number;
    p50_latency_ms: number;
    p95_latency_ms: number;
    avg_confidence: number;
    avg_detections: number;
    error_count: number;
  };
  by_model: {
    model_id: string | null;
    model_name: string | null;
    request_count: number;
    avg_latency_ms: number;
    avg_confidence: number;
  }[];
  by_class: {
    class_name: string;
    detection_count: number;
  }[];
  by_target: {
    target_id: string | null;
    target_name: string | null;
    location_label: string | null;
    request_count: number;
    avg_latency_ms: number;
    avg_confidence: number;
    last_seen: string | null;
  }[];
  timeseries: {
    timestamp: string;
    requests: number;
    avg_latency_ms: number;
    avg_confidence: number;
    avg_detections: number;
  }[];
}

export async function getMetricsSummary(window: string = "1h"): Promise<MetricsSummary> {
  const res = await authFetch(`${BASE}/metrics/summary?window=${window}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}


// ── Model Promotion ─────────────────────────────────────────────

export async function promoteModel(modelId: string, alias: string = "champion"): Promise<{ status: string; alias: string }> {
  const res = await authFetch(`${BASE}/models/${modelId}/promote?alias=${alias}`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}


// ── Deployment Experiments (Blue-Green) ─────────────────────────

export interface DeploymentExperiment {
  id: string;
  name: string;
  champion_model_id: string;
  champion_name: string | null;
  challenger_model_id: string;
  challenger_name: string | null;
  split_pct: number;
  status: string;
  target_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  winner: string | null;
}

export async function listExperiments(): Promise<DeploymentExperiment[]> {
  const res = await authFetch(`${BASE}/experiments`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createExperiment(data: {
  name: string;
  champion_model_id: string;
  challenger_model_id: string;
  split_pct?: number;
  target_id?: string;
}): Promise<{ id: string; status: string }> {
  const res = await authFetch(`${BASE}/experiments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function completeExperiment(experimentId: string, winner: string): Promise<{ status: string }> {
  const res = await authFetch(`${BASE}/experiments/${experimentId}/complete?winner=${winner}`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}


// ── Edge Devices ────────────────────────────────────────────────

export interface EdgeDevice {
  id: string;
  name: string;
  device_type: string;
  location_label: string | null;
  target_id: string | null;
  model_id: string | null;
  model_version: number | null;
  hardware_info: Record<string, unknown>;
  status: string;
  last_heartbeat: string | null;
  last_sync: string | null;
  ip_address: string | null;
}

export async function listDevices(): Promise<EdgeDevice[]> {
  const res = await authFetch(`${BASE}/devices`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function registerDevice(data: {
  name: string;
  device_type: string;
  location_label?: string;
  target_id?: string;
  model_id?: string;
  hardware_info?: Record<string, unknown>;
}): Promise<{ id: string }> {
  const res = await authFetch(`${BASE}/devices`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}


// ── Comparison Runs (Benchmark History) ─────────────────────────

export interface ComparisonRun {
  id: string;
  name: string;
  file_name: string;
  is_video: boolean;
  sam_prompts: string[] | null;
  confidence_threshold: number;
  model_a_id: string | null;
  model_a_name: string;
  model_a_detections: number;
  model_a_avg_confidence: number | null;
  model_a_latency_ms: number;
  model_b_id: string | null;
  model_b_name: string;
  model_b_detections: number;
  model_b_avg_confidence: number | null;
  model_b_latency_ms: number;
  notes: string | null;
  created_at: string;
}

export async function listComparisons(): Promise<ComparisonRun[]> {
  const res = await authFetch(`${BASE}/comparisons`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function saveComparison(data: Omit<ComparisonRun, "id" | "created_at">): Promise<{ id: string }> {
  const res = await authFetch(`${BASE}/comparisons`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteComparison(id: string): Promise<{ status: string }> {
  const res = await authFetch(`${BASE}/comparisons/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}


// ── Background Comparison Task ──────────────────────────────────

export interface CompareSession {
  session_id: string;
  celery_task_id: string;
  file_name: string;
  is_video: boolean;
}

export async function startComparison(
  file: File,
  modelAId: string,
  modelBId: string,
  conf: number,
  samPrompts?: string[],
): Promise<CompareSession> {
  const form = new FormData();
  form.append("file", file);
  let url = `${BASE}/comparisons/run?model_a_id=${encodeURIComponent(modelAId)}&model_b_id=${encodeURIComponent(modelBId)}&conf=${conf}`;
  if (samPrompts && samPrompts.length > 0) url += `&sam_prompts=${encodeURIComponent(samPrompts.join(","))}`;
  const res = await authFetch(url, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export interface CompareResultResponse {
  status: "completed" | "running";
  session_id: string;
  results?: {
    a: { dets: DetectionOut[]; frames: FrameResultOut[] | null; latency: number; error: string | null };
    b: { dets: DetectionOut[]; frames: FrameResultOut[] | null; latency: number; error: string | null };
  };
}

export async function getComparisonResult(sessionId: string): Promise<CompareResultResponse> {
  const res = await authFetch(`${BASE}/comparisons/result/${sessionId}`);
  if (res.status === 202) return { status: "running", session_id: sessionId };
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ─── Admin — queue / worker management ─────────────────────────

export interface AdminWorker {
  name: string;
  uptime_seconds: number | null;
  active_tasks: Array<{
    id: string | null;
    name: string;
    job_id?: string;
    run_id?: string;
    prompt?: string | null;
    variant?: string | null;
    elapsed_seconds: number | null;
  }>;
  reserved_tasks: number;
  heartbeat_age_seconds: number | null;
  pool: string | null;
}

export interface AdminQueue {
  name: string;
  pending: number;
}

export interface StuckJob {
  id: string;
  text_prompt: string | null;
  status: string;
  age_seconds: number;
  celery_task_id: string | null;
  project_id: string | null;
  progress: number | null;
}

export interface AdminStatus {
  workers: AdminWorker[];
  queues: AdminQueue[];
  stuck_jobs: StuckJob[];
  stuck_threshold_seconds: number;
}

export async function getAdminStatus(stuckThresholdSeconds = 600): Promise<AdminStatus> {
  const res = await authFetch(`${BASE}/admin/status?stuck_threshold=${stuckThresholdSeconds}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function revokeTask(taskId: string, terminate = true): Promise<void> {
  const res = await authFetch(`${BASE}/admin/tasks/${taskId}/revoke?terminate=${terminate}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function purgeQueue(queueName: string): Promise<{ removed: number }> {
  const res = await authFetch(`${BASE}/admin/queue/${queueName}/purge`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function markJobFailed(jobId: string, reason?: string): Promise<void> {
  const qs = reason ? `?reason=${encodeURIComponent(reason)}` : "";
  const res = await authFetch(`${BASE}/admin/jobs/${jobId}/mark-failed${qs}`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
}
