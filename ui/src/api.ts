const BASE = "/api/v1";

/** Get auth headers — injects JWT token from localStorage into all API calls. */
function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("waldo_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Authenticated fetch wrapper. */
async function authFetch(url: string, init?: RequestInit): Promise<Response> {
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
  video_id: string;
  text_prompt: string;
  status: string;
  progress: number;
  total_frames: number;
  processed_frames: number;
  result_url: string | null;
  error_message: string | null;
  celery_task_id: string | null;
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
  status?: string
): Promise<AnnotationOut[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
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
}

export async function getDatasetOverview(jobId: string): Promise<DatasetOverview> {
  const res = await authFetch(`${BASE}/jobs/${jobId}/overview`);
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
}

export interface ClassPrompt {
  name: string;
  prompt: string;
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
  loss_history: Record<string, number>[];
  metric_history: Record<string, number>[];
  weights_url: string | null;
  error_message: string | null;
  celery_task_id: string | null;
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

export async function predictImage(file: File, conf = 0.25, classes?: string[]): Promise<ImagePredictionResponse> {
  const form = new FormData();
  form.append("file", file);
  let url = `${BASE}/predict/image?conf=${conf}`;
  if (classes && classes.length > 0) url += `&classes=${encodeURIComponent(classes.join(","))}`;
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
): Promise<VideoPredictionResponse | AsyncVideoResult> {
  const form = new FormData();
  form.append("file", file);
  let url = `${BASE}/predict/video?conf=${conf}`;
  if (classes && classes.length > 0) url += `&classes=${encodeURIComponent(classes.join(","))}`;
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
}

export interface FeedbackOut {
  id: string;
  feedback_type: string;
  class_name: string;
  confidence: number | null;
  bbox: number[] | null;
  track_id: number | null;
  frame_index: number | null;
  source_filename: string | null;
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
