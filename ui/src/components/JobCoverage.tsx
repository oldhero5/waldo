import type { JobStatus } from "../api";
import { isTerminalJobStatus } from "../lib/jobStatus";

const time = (value: number | null) => value != null && Number.isFinite(value) ? `${value.toFixed(2)}s` : "unknown";

export default function JobCoverage({ job }: { job: JobStatus }) {
  const summary = job.processing_summary;
  const videos = summary?.videos || [];
  const sampleCount = videos.reduce((count, video) => count + (video.sampled_frames || 0), 0);
  const failed = videos.filter((video) => video.status === "failed");
  return (
    <section aria-label="Processing coverage" className="mt-3 mb-3 space-y-2 text-sm" style={{ color: "var(--text-secondary)" }}>
      {job.status === "partial" && <p>Some clips failed. Available evidence can be reviewed; processing is incomplete.</p>}
      {job.status === "failed" && <p>Processing failed. Any existing evidence remains available for review.</p>}
      <p>{summary?.coverage === "sampled"
        ? `Sampled frames assessed: ${sampleCount}. This does not establish full-video coverage.`
        : "Processing coverage is unknown for this job."}</p>
      {Boolean(summary?.merged_runs?.length) && <p>Additional labeling runs contributed annotations. Coverage shown here describes the original run.</p>}
      {summary?.requested_sample_fps != null && <p>Requested sampling: {summary.requested_sample_fps} fps.</p>}
      {videos.length > 0 && (
        <details open={failed.length > 0}>
          <summary className="cursor-pointer">Clip assessments ({videos.length})</summary>
          <ul className="mt-2 space-y-2">
            {videos.map((video) => (
              <li key={video.video_id}>
                <p>Clip {video.video_id}: {video.status}{video.sampled_frames != null ? ` · ${video.sampled_frames} assessed samples` : ""}</p>
                {video.error && <p style={{ color: "var(--danger)" }}>{video.error}</p>}
                <p>Assessed timestamps: {video.assessed_timestamps_s?.length
                  ? `${video.assessed_timestamps_s.slice(0, 6).map(time).join(", ")}${video.assessed_timestamps_s.length > 6 ? `, and ${video.assessed_timestamps_s.length - 6} more` : ""} (approximate)`
                  : "unknown"}.</p>
                {video.source_fps != null && video.sampling_stride != null && video.sampling_stride > 0 && (
                  <p>Effective sampling: {(video.source_fps / video.sampling_stride).toFixed(2)} fps.</p>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {job.error_message && !failed.length && <p style={{ color: "var(--danger)" }}>{job.error_message}</p>}
      {job.status === "completed" && !job.result_url && <p>Export dataset before training.</p>}
      {job.status !== "completed" && isTerminalJobStatus(job.status) && <p>Training shortcuts are available after a completed job.</p>}
    </section>
  );
}
