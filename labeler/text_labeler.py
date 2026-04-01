"""Text-prompt labeling pipeline — uses Sam3VideoModel for detect-and-track."""
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from labeler.frame_extractor import extract_frames
from labeler.pipeline import _update_job, convert_and_store
from labeler.sam3_engine import SegmentationResult, get_engine
from lib.db import Frame, LabelingJob, SessionLocal, Video
from lib.storage import download_file, upload_file


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

        merged.append(SegmentationResult(
            frame_index=frame_idx,
            masks=masks,
            boxes=boxes,
            scores=scores,
            class_indices=class_indices,
        ))

    return merged


def _resolve_class_prompts(job: LabelingJob) -> list[dict]:
    """Get class prompts from job, falling back to text_prompt for backward compat."""
    if job.class_prompts:
        return job.class_prompts
    return [{"name": job.text_prompt, "prompt": job.text_prompt}]


def _process_single_video(session, job, video, engine, tmpdir, class_prompts, frame_offset=0, total_frame_estimate=0):
    """Extract frames, run SAM3 (with multiclass), return (seg_results, db_frames, frame_infos)."""
    video_path = tmpdir / video.filename
    download_file(video.minio_key, video_path)

    frames_dir = tmpdir / f"frames_{video.id}"
    frame_infos = extract_frames(video_path, frames_dir)

    # Upload frames to MinIO and record in DB
    db_frames: list[Frame] = []
    for fi in frame_infos:
        minio_key = f"frames/{job.id}/{video.id}_{fi.file_path.name}"
        upload_file(minio_key, fi.file_path)

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
    session.commit()

    # SAM3 segmentation — once per class, then merge
    images = [Image.open(fi.file_path) for fi in frame_infos]

    if len(class_prompts) == 1:
        seg_results = engine.segment_frames(images, class_prompts[0]["prompt"])
        # Tag with class index 0
        for sr in seg_results:
            sr.class_indices = np.zeros(sr.masks.shape[0], dtype=int)
    else:
        per_class_results = []
        for cls_idx, cp in enumerate(class_prompts):
            cls_results = engine.segment_frames(images, cp["prompt"])
            for sr in cls_results:
                sr.class_indices = np.full(sr.masks.shape[0], cls_idx, dtype=int)
            per_class_results.append(cls_results)
        seg_results = merge_multiclass_results(per_class_results, len(images))

    return seg_results, db_frames, frame_infos


def run_labeling_pipeline(celery_task, job_id: str) -> dict:
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).one()
        class_prompts = _resolve_class_prompts(job)
        class_names = [cp["name"] for cp in class_prompts]

        # Determine videos to process
        if job.project_id and not job.video_id:
            videos = session.query(Video).filter_by(project_id=job.project_id).all()
        else:
            videos = [job.video]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Phase 1: Extract frames for all videos
            _update_job(session, job, status="extracting")
            celery_task.update_state(state="EXTRACTING")

            # Estimate total frames across all videos
            total_estimate = sum(v.frame_count or 0 for v in videos)
            _update_job(session, job, total_frames=total_estimate)

            # Phase 2: Process each video
            _update_job(session, job, status="labeling")
            celery_task.update_state(state="LABELING")

            engine = get_engine()
            all_seg_results = []
            all_db_frames = []
            all_frame_infos = []
            frame_offset = 0

            for video in videos:
                seg_results, db_frames, frame_infos = _process_single_video(
                    session, job, video, engine, tmpdir, class_prompts,
                    frame_offset=frame_offset,
                    total_frame_estimate=total_estimate,
                )

                # Re-index frame_index to be global across all videos
                for i, sr in enumerate(seg_results):
                    sr.frame_index = frame_offset + i

                all_seg_results.extend(seg_results)
                all_db_frames.extend(db_frames)
                all_frame_infos.extend(frame_infos)
                frame_offset += len(frame_infos)

            # Update total_frames with actual count
            _update_job(session, job, total_frames=len(all_frame_infos))

            # Phase 3: Convert + store + package
            _update_job(session, job, status="converting")
            celery_task.update_state(state="CONVERTING")

            result_key = convert_and_store(
                session, job, all_seg_results, all_db_frames, all_frame_infos, class_names, tmpdir
            )

            _update_job(session, job, status="completed", result_minio_key=result_key, progress=1.0)
            return {"status": "completed", "result_minio_key": result_key}

    except Exception as e:
        session.rollback()
        try:
            job = session.query(LabelingJob).filter_by(id=job_id).one()
            _update_job(session, job, status="failed", error_message=str(e))
        except Exception:
            pass
        raise
    finally:
        session.close()
