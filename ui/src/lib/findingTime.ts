import type { AnnotationOut } from "../api";

export function findingTime(annotation: AnnotationOut): {
  label: string;
  compactLabel: string;
  time: number | null;
} {
  const time = annotation.timestamp_s;
  if (time == null)
    return { label: "Time unknown · no automatic seek", compactLabel: "Time unknown", time: null };
  if (!Number.isFinite(time) || time < 0)
    return { label: "Invalid time · no automatic seek", compactLabel: "Invalid time", time: null };
  if (annotation.timestamp_method === "source_pts")
    return {
      label: `Recorded time ${time.toFixed(2)}s`,
      compactLabel: `${time.toFixed(2)}s`,
      time,
    };
  if (
    annotation.timestamp_method === "frame_index/fps" ||
    annotation.timestamp_method === "resampled_ordinal/fps"
  ) {
    return {
      label: `Approximate time ${time.toFixed(2)}s`,
      compactLabel: `Approx. ${time.toFixed(2)}s`,
      time,
    };
  }
  return { label: "Time unknown · no automatic seek", compactLabel: "Time unknown", time: null };
}
