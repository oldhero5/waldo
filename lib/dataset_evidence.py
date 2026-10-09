"""Frame counts derive from a labeling run, never all frames in its project."""


def assessed_frame_count(job, annotated_frames: int) -> int:
    summary = job.processing_summary or {}
    videos = summary.get("videos")
    if isinstance(videos, list):
        assessed = sum(
            entry.get("sampled_frames", 0)
            for entry in videos
            if isinstance(entry, dict) and isinstance(entry.get("sampled_frames"), int) and entry["sampled_frames"] >= 0
        )
    else:
        assessed = job.total_frames or 0
    return max(assessed, annotated_frames)
