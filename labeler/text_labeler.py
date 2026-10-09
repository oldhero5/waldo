"""Text-prompt labeling pipeline — uses Sam3VideoModel for detect-and-track."""

import copy
import logging
import math
import tempfile
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from PIL import Image

from labeler.errors import RetryableLabelingError, is_retryable
from labeler.frame_extractor import extract_frames
from labeler.pipeline import _update_job, replace_raw_observations
from labeler.sam3_engine import SegmentationResult, get_engine
from lib.config import settings
from lib.db import Annotation, Frame, LabelingJob, SessionLocal, Video
from lib.storage import download_file, upload_file

logger = logging.getLogger(__name__)

# Absolute bounds for the computed stride.
_MIN_STRIDE = 1
_MAX_STRIDE = 30


def _compute_stride(total_frames: int, detect_every: int | None) -> int:
    """Return the frame-sampling stride.

    Explicit stride is clamped to [_MIN_STRIDE, _MAX_STRIDE]. Otherwise preserve
    every frame returned by the requested extraction rate.
    """
    if detect_every is not None:
        return max(_MIN_STRIDE, min(_MAX_STRIDE, detect_every))
    return 1


def merge_multiclass_results(
    per_class_results: list[list[SegmentationResult]],
    num_frames: int,
) -> list[SegmentationResult]:
    """Merge per-class segmentation results into a single list.

    For each frame, concatenate masks/boxes/scores/class_indices from all classes.
    """
    merged: list[SegmentationResult] = []
    for frame_idx in range(num_frames):
        all_masks = []
        all_boxes = []
        all_scores = []
        all_cls_indices = []

        for cls_results in per_class_results:
            sr = cls_results[frame_idx]
            if sr.masks.shape[0] > 0:
                all_masks.append(sr.masks)
                all_boxes.append(sr.boxes)
                all_scores.append(sr.scores)
                if sr.class_indices is not None:
                    all_cls_indices.append(sr.class_indices)
                else:
                    all_cls_indices.append(np.zeros(sr.masks.shape[0], dtype=int))

        if all_masks:
            masks = np.concatenate(all_masks, axis=0)
            boxes = np.concatenate(all_boxes, axis=0)
            scores = np.concatenate(all_scores, axis=0)
            class_indices = np.concatenate(all_cls_indices, axis=0)
        else:
            h, w = per_class_results[0][frame_idx].masks.shape[1:]
            masks = np.empty((0, h, w), dtype=bool)
            boxes = np.empty((0, 4), dtype=np.float32)
            scores = np.empty(0, dtype=np.float32)
            class_indices = np.empty(0, dtype=int)

        merged.append(
            SegmentationResult(
                frame_index=frame_idx,
                masks=masks,
                boxes=boxes,
                scores=scores,
                class_indices=class_indices,
            )
        )

    return merged


def _resolve_class_prompts(job: LabelingJob) -> list[dict]:
    """Get class prompts from job, falling back to text_prompt for backward compat."""
    if job.class_prompts:
        return job.class_prompts
    return [{"name": job.text_prompt, "prompt": job.text_prompt}]


def _process_single_video(
    session,
    job,
    video,
    engine,
    tmpdir,
    class_prompts,
    frame_offset=0,
    total_frame_estimate=0,
    detect_every: int | None = None,
):
    """Extract frames, run SAM3 (with multiclass), return (seg_results, db_frames, frame_infos).

    Args:
        detect_every: Optional explicit stride over the sampled frames.
            None preserves every frame returned at the job's requested rate.
    """
    video_path = tmpdir / f"{video.id}_{Path(video.filename).name}"
    download_file(video.minio_key, video_path)

    frames_dir = tmpdir / f"frames_{video.id}"
    sample_fps = getattr(job, "sample_fps", None)
    sample_fps = sample_fps if sample_fps is not None else 1.0
    frame_infos = extract_frames(video_path, frames_dir, fps=sample_fps, dedup_threshold=-1, use_cache=False)
    if not frame_infos:
        raise ValueError(f"No sampled frames decoded for video {video.id}")

    # Apply only an explicitly requested additional stride.
    total_frames = len(frame_infos)
    stride = _compute_stride(total_frames, detect_every)
    if stride > 1:
        logger.info(
            "Frame-skip enabled: %d total frames → sampling every %d frames (~%d processed)",
            total_frames,
            stride,
            math.ceil(total_frames / stride),
        )
        frame_infos = frame_infos[::stride]

    # Upload frames to MinIO and record in DB
    db_frames: list[Frame] = []
    for fi in frame_infos:
        minio_key = f"frames/{job.id}/fps_{sample_fps:g}/{video.id}_{fi.file_path.name}"
        upload_file(minio_key, fi.file_path)

        db_frame = session.query(Frame).filter_by(video_id=video.id, minio_key=minio_key).first()
        if db_frame is None:
            db_frame = Frame(
                video_id=video.id,
                frame_number=fi.frame_number,
                timestamp_s=fi.timestamp_s,
                minio_key=minio_key,
                phash=fi.phash,
                width=fi.width,
                height=fi.height,
            )
            session.add(db_frame)
        db_frames.append(db_frame)
    session.flush()

    # SAM3 segmentation — once per prompt alias, then merge.
    # Use config-backed threshold; per-call override capability preserved via
    # engine.segment_frames(threshold=...) when needed.
    score_threshold = getattr(job, "score_threshold", None)
    if score_threshold is None:
        score_threshold = settings.sam3_score_threshold
    class_names = []
    for cp in class_prompts:
        if cp["name"] not in class_names:
            class_names.append(cp["name"])

    # Build flat list of (prompt_str, class_idx) for all aliases
    prompt_runs: list[tuple[str, int]] = []
    for cp in class_prompts:
        cls_idx = class_names.index(cp["name"])
        aliases = cp.get("prompts") or [cp.get("prompt", cp["name"])]
        for alias in aliases:
            prompt_runs.append((alias, cls_idx))

    with ExitStack() as stack:
        images = [stack.enter_context(Image.open(fi.file_path)) for fi in frame_infos]
        per_prompt_results = []
        for prompt_str, cls_idx in prompt_runs:
            cls_results = engine.segment_frames(images, prompt_str, threshold=score_threshold)
            if len(cls_results) != len(images):
                raise ValueError("Segmentation and sampled-frame list lengths must match")
            for sr in cls_results:
                sr.class_indices = np.full(sr.masks.shape[0], cls_idx, dtype=int)
            per_prompt_results.append(cls_results)
        if not per_prompt_results:
            raise ValueError("At least one labeling prompt is required")
        seg_results = (
            per_prompt_results[0]
            if len(per_prompt_results) == 1
            else merge_multiclass_results(per_prompt_results, len(images))
        )

    return seg_results, db_frames, frame_infos


def run_labeling_pipeline(celery_task, job_id: str) -> dict:
    """Persist observations for explicit review/export; reuse committed clips on retry."""
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).one()
        if getattr(job, "status", None) == "completed":
            return {
                "status": "completed",
                "result_minio_key": getattr(job, "result_minio_key", None),
                "processing_summary": job.processing_summary or {},
            }
        class_prompts = _resolve_class_prompts(job)
        class_names = list(dict.fromkeys(cp["name"] for cp in class_prompts))
        videos = (
            session.query(Video).filter_by(project_id=job.project_id).all()
            if job.project_id and not job.video_id
            else [job.video]
        )
        videos = [video for video in videos if video is not None]
        sample_fps = job.sample_fps if job.sample_fps is not None else 1.0
        threshold = job.score_threshold if job.score_threshold is not None else settings.sam3_score_threshold
        summary = (
            copy.deepcopy(job.processing_summary)
            if job.processing_summary
            else {
                "backend": "pytorch",
                "coverage": "sampled",
                "timestamp_method": "resampled_ordinal/fps",
                "requested_sample_fps": sample_fps,
                "score_threshold": threshold,
                "decode_gap_assessment": "ffmpeg_errors_fail_clip; source_indices_unavailable",
                "videos": [],
            }
        )
        for key, value in (("score_threshold", threshold), ("requested_sample_fps", sample_fps)):
            if key in summary and summary[key] != value:
                raise ValueError(f"Retry configuration differs from recorded {key}; start a new labeling job")
        completed = {entry["video_id"]: entry for entry in summary.get("videos", []) if entry["status"] == "completed"}
        for video in videos:
            if str(video.id) not in completed:
                source_frames = session.query(Frame.id).filter_by(video_id=video.id)
                existing = (
                    session.query(Annotation.id)
                    .filter(Annotation.job_id == job.id, Annotation.frame_id.in_(source_frames))
                    .first()
                )
                if existing is not None:
                    raise ValueError("Existing observations lack a completed clip summary; start a new labeling job")
        summary["videos"] = [completed[str(video.id)] for video in videos if str(video.id) in completed]
        successful = len(summary["videos"])
        frames_processed = sum(entry.get("sampled_frames", 0) for entry in summary["videos"])
        _update_job(
            session,
            job,
            status="labeling",
            progress=successful / len(videos) if videos else 0,
            processed_frames=frames_processed,
            total_frames=frames_processed,
            processing_summary=summary,
            error_message=None,
        )
        celery_task.update_state(state="LABELING")
        with tempfile.TemporaryDirectory() as temporary:
            tmpdir = Path(temporary)
            engine = get_engine() if any(str(video.id) not in completed for video in videos) else None
            for video in videos:
                if str(video.id) in completed:
                    continue
                entry = {"video_id": str(video.id)}
                try:
                    results, frames, infos = _process_single_video(
                        session,
                        job,
                        video,
                        engine,
                        tmpdir,
                        class_prompts,
                        frame_offset=frames_processed,
                    )
                    if not (len(results) == len(frames) == len(infos)):
                        raise ValueError("Segmentation, database-frame and source-frame list lengths must match")
                    replace_raw_observations(session, job, results, frames, class_names)
                    entry.update(
                        status="completed",
                        sampled_frames=len(infos),
                        assessed_timestamps_s=[info.timestamp_s for info in infos],
                        timestamp_method="resampled_ordinal/fps",
                    )
                    next_summary = {**summary, "videos": [*summary["videos"], entry]}
                    # Observations and completion metadata must commit together.
                    # New evidence invalidates a previous partial dataset export.
                    _update_job(
                        session,
                        job,
                        processing_summary=next_summary,
                        result_minio_key=None,
                        processed_frames=frames_processed + len(infos),
                        total_frames=frames_processed + len(infos),
                        progress=(successful + 1) / len(videos),
                    )
                    summary = next_summary
                    frames_processed += len(infos)
                    successful += 1
                except Exception as error:
                    session.rollback()
                    entry = {"video_id": str(video.id)}
                    logger.exception("Labeling failed for video %s", video.id)
                    if is_retryable(error):
                        entry.update(status="retrying", error=str(error))
                        summary["videos"].append(entry)
                        _update_job(
                            session,
                            job,
                            status="retrying",
                            processing_summary=summary,
                            error_message=str(error),
                            processed_frames=frames_processed,
                        )
                        raise RetryableLabelingError(str(error)) from error
                    entry.update(status="failed", error=str(error))
                if entry["status"] != "completed":
                    summary["videos"].append(entry)
                    _update_job(
                        session,
                        job,
                        processing_summary=summary,
                        progress=successful / len(videos),
                        total_frames=frames_processed,
                    )
            failures = [entry for entry in summary["videos"] if entry["status"] == "failed"]
            status = "completed" if videos and not failures else "partial" if successful else "failed"
            error_message = "; ".join(f"{entry['video_id']}: {entry['error']}" for entry in failures) or (
                "No videos to process" if not videos else None
            )
            _update_job(
                session,
                job,
                status=status,
                progress=successful / len(videos) if videos else 0,
                processed_frames=frames_processed,
                total_frames=frames_processed,
                processing_summary=summary,
                error_message=error_message,
            )
            return {
                "status": status,
                "result_minio_key": getattr(job, "result_minio_key", None),
                "processing_summary": summary,
            }
    except Exception as error:
        session.rollback()
        retryable = is_retryable(error)
        try:
            job = session.query(LabelingJob).filter_by(id=job_id).one()
            _update_job(session, job, status="retrying" if retryable else "failed", error_message=str(error))
        except Exception:
            pass
        if retryable and not isinstance(error, RetryableLabelingError):
            raise RetryableLabelingError(str(error)) from error
        raise
    finally:
        session.close()
