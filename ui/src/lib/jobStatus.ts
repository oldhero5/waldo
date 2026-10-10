import type { JobStatus } from "../api";

export const isTerminalJobStatus = (status: string) => ["completed", "partial", "failed", "cancelled"].includes(status);

export function jobCompletionProgress(job: JobStatus): number | null {
  if (job.status === "completed") return 1;
  const videos = job.processing_summary?.videos;
  const value = videos?.length
    ? videos.filter((video) => video.status === "completed").length / videos.length
    : job.progress;
  return Number.isFinite(value) && value >= 0 && value < 1 ? value : null;
}


export const hasTrainingArtifact = (job: JobStatus) => job.status === "completed" && !!job.result_url;
