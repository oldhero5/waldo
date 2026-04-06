"""Video-native labeling pipeline using SAM3.1 MLX.

Processes videos directly without frame extraction. Uses backbone caching and
object tracking for efficient multi-object detection across video frames.
Streams detections to Redis for live UI updates.
"""
import json
import logging
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from labeler.pipeline import _update_job
from lib.config import settings
from lib.db import Annotation, Frame, LabelingJob, SessionLocal, Video
from lib.storage import download_file, upload_file

logger = logging.getLogger(__name__)


def _publish_detection(job_id: str, video_name: str, frame_idx: int, detections: list[dict]):
    """Publish detections to Redis for live UI streaming."""
    try:
        import redis
        r = redis.from_url(settings.redis_url)
        r.publish(f"waldo:labeling:{job_id}", json.dumps({
            "type": "detection",
            "video": video_name,
            "frame_idx": frame_idx,
            "detections": detections,
        }))
    except Exception:
        pass  # Non-critical — UI streaming is best-effort


def _publish_progress(
    job_id: str, videos_done: int, videos_total: int, video_name: str,
    avg_seconds: float = 0, eta_seconds: float = 0, annotations_so_far: int = 0,
):
    """Publish progress update with ETA to Redis."""
    try:
        import redis
        r = redis.from_url(settings.redis_url)
        r.publish(f"waldo:labeling:{job_id}", json.dumps({
            "type": "progress",
            "videos_done": videos_done,
            "videos_total": videos_total,
            "current_video": video_name,
            "progress": videos_done / max(1, videos_total),
            "avg_seconds_per_video": round(avg_seconds, 1),
            "eta_seconds": round(eta_seconds),
            "annotations": annotations_so_far,
        }))
    except Exception:
        pass


_cached_predictor = None


def _get_predictor(threshold: float = 0.15, resolution: int = 1008):
    """Cached SAM3.1 MLX predictor — loaded once, reused across videos."""
    global _cached_predictor
    if _cached_predictor is None:
        from mlx_vlm.models.sam3.generate import Sam3Predictor
        from mlx_vlm.models.sam3_1.processing_sam3_1 import Sam31Processor
        from mlx_vlm.utils import get_model_path, load_model

        model_path = settings.sam3_mlx_model_id
        mp = get_model_path(model_path)
        model = load_model(mp)
        processor = Sam31Processor.from_pretrained(str(mp))
        if resolution != 1008:
            processor.image_size = resolution
        _cached_predictor = Sam3Predictor(model, processor, score_threshold=threshold)
        logger.info("SAM3.1 MLX predictor loaded and cached")
    return _cached_predictor


def _result_to_detections(result, W: int, H: int, prompts: list[str]) -> list[dict]:
    """Convert a DetectionResult to serializable detection dicts with polygons."""
    det_list = []
    for i in range(len(result.scores)):
        mask = result.masks[i] if i < len(result.masks) else None
        polygon = None
        if mask is not None:
            mask_u8 = (mask > 0.5).astype(np.uint8) * 255
            if mask_u8.shape != (H, W):
                mask_u8 = cv2.resize(mask_u8, (W, H), interpolation=cv2.INTER_NEAREST)
            contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                largest = max(contours, key=cv2.contourArea)
                if cv2.contourArea(largest) > 50:
                    eps = 0.001 * cv2.arcLength(largest, True)
                    approx = cv2.approxPolyDP(largest, eps, True)
                    if len(approx) >= 3:
                        pts = approx.reshape(-1, 2)
                        polygon = []
                        for px, py in pts:
                            polygon.append(float(px / W))
                            polygon.append(float(py / H))

        det_list.append({
            "bbox": result.boxes[i].tolist() if i < len(result.boxes) else None,
            "score": float(result.scores[i]),
            "label": result.labels[i] if result.labels and i < len(result.labels) else prompts[0],
            "track_id": int(result.track_ids[i]) if result.track_ids is not None and i < len(result.track_ids) else None,
            "polygon": polygon,
        })
    return det_list


def process_video_native(
    video_path: str,
    prompts: list[str],
    threshold: float = 0.35,
    detect_every: int = 30,
    backbone_every: int = 4,
    resolution: int = 1008,
) -> list[dict]:
    """Process a video with SAM3.1 MLX — detect-only with aggressive frame skip.

    Runs DETR detection every `detect_every` frames with backbone caching.
    SimpleTracker maintains object IDs across detections via IoU matching.

    With detect_every=30 on a 4320-frame video:
    - 144 DETR calls × ~150ms = 22s
    - 144 frame reads × ~3ms = 0.4s
    - 144 preprocess × ~20ms = 3s
    - Total: ~25-30s per video
    """
    import mlx.core as mx
    from mlx_vlm.generate import wired_limit
    from mlx_vlm.models.sam3.generate import SimpleTracker
    from mlx_vlm.models.sam3_1.generate import _get_backbone_features

    from labeler.sam3_optimized import detect_with_backbone_fast as _detect_with_backbone

    predictor = _get_predictor(threshold, resolution)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    tracker = SimpleTracker()
    backbone_cache = None
    encoder_cache = {}
    detect_count = 0
    results = []

    with wired_limit(predictor.model):
        for fi in range(total_frames):
            ret, frame_bgr = cap.read()
            if not ret:
                break

            if fi % detect_every != 0:
                continue

            frame_pil = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
            inputs = predictor.processor.preprocess_image(frame_pil)
            pixel_values = mx.array(inputs["pixel_values"])

            if detect_count % backbone_every == 0 or backbone_cache is None:
                backbone_cache = _get_backbone_features(predictor.model, pixel_values)
                encoder_cache.clear()

            result = _detect_with_backbone(
                predictor, backbone_cache, prompts, frame_pil.size, threshold,
                encoder_cache=encoder_cache,
            )
            result = tracker.update(result)
            detect_count += 1

            if len(result.scores) > 0:
                results.append({
                    "frame_idx": fi,
                    "timestamp_s": fi / fps,
                    "width": W,
                    "height": H,
                    "detections": _result_to_detections(result, W, H, prompts),
                })

    cap.release()
    total_dets = sum(len(r["detections"]) for r in results)
    logger.info("Processed %s: %d frames, %d detects, %d result frames, %d detections",
                video_path, total_frames, detect_count, len(results), total_dets)
    return results


def run_video_labeling_pipeline(celery_task, job_id: str) -> dict:
    """Main labeling pipeline — processes videos natively with SAM3.1.

    Progress tracked by video (not frame). Detections streamed to Redis.
    """
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).one()
        class_prompts = job.class_prompts or [{"name": job.text_prompt, "prompt": job.text_prompt}]
        prompts = [cp["prompt"] for cp in class_prompts]
        class_names = [cp["name"] for cp in class_prompts]

        # Get videos
        if job.project_id and not job.video_id:
            videos = session.query(Video).filter_by(project_id=job.project_id).all()
        elif job.video_id:
            videos = [session.query(Video).filter_by(id=job.video_id).one()]
        else:
            _update_job(session, job, status="failed", error_message="No videos to process")
            return {"status": "failed"}

        _update_job(session, job, status="labeling", total_frames=len(videos), processed_frames=0, progress=0.0)
        celery_task.update_state(state="LABELING")

        all_annotations = []
        video_times: list[float] = []  # seconds per video for ETA calc
        import time as _time

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            for vid_idx, video in enumerate(videos):
                vid_start = _time.perf_counter()
                video_path = tmpdir / video.filename
                download_file(video.minio_key, video_path)

                # Calculate ETA from running average
                avg_s = sum(video_times) / max(1, len(video_times)) if video_times else 0
                remaining = len(videos) - vid_idx
                eta_s = remaining * avg_s if avg_s > 0 else 0

                # Store avg in Redis for the overview API to read
                if avg_s > 0:
                    try:
                        import redis
                        r = redis.from_url(settings.redis_url)
                        r.set(f"waldo:labeling:avg:{job_id}", str(round(avg_s, 1)), ex=3600)
                    except Exception:
                        pass

                _publish_progress(
                    job_id, vid_idx, len(videos), video.filename,
                    avg_seconds=avg_s, eta_seconds=eta_s, annotations_so_far=len(all_annotations),
                )

                try:
                    frame_results = process_video_native(
                        str(video_path), prompts,
                        threshold=0.35,
                    )
                except Exception as e:
                    logger.warning("Failed to process %s: %s", video.filename, e)
                    continue

                # Deduplicate by track_id — keep best detection per tracked object
                # Each unique track_id = one real-world object across the video
                best_by_track: dict[int, dict] = {}  # track_id -> best detection info
                for fr in frame_results:
                    for det in fr["detections"]:
                        if not det.get("polygon"):
                            continue
                        tid = det.get("track_id", -1)
                        if tid is None:
                            tid = -1
                        existing_best = best_by_track.get(tid)
                        if existing_best is None or det["score"] > existing_best["score"]:
                            best_by_track[tid] = {
                                **det,
                                "frame_idx": fr["frame_idx"],
                                "timestamp_s": fr["timestamp_s"],
                                "width": fr["width"],
                                "height": fr["height"],
                            }

                unique_objects = list(best_by_track.values())
                logger.info("Video %s: %d total detections → %d unique tracked objects",
                            video.filename, sum(len(fr["detections"]) for fr in frame_results), len(unique_objects))

                # Create frame + annotation for each unique tracked object
                live_dets = []
                for det in unique_objects:
                    # Get or create frame for this detection's best frame
                    existing_frame = session.query(Frame).filter_by(
                        video_id=video.id, frame_number=det["frame_idx"]
                    ).first()

                    if not existing_frame:
                        cap = cv2.VideoCapture(str(video_path))
                        cap.set(cv2.CAP_PROP_POS_FRAMES, det["frame_idx"])
                        ret, frame_bgr = cap.read()
                        cap.release()

                        if not ret:
                            continue

                        frame_path = tmpdir / f"frame_{video.id}_{det['frame_idx']}.jpg"
                        cv2.imwrite(str(frame_path), frame_bgr)
                        minio_key = f"frames/{job_id}/{video.id}_{det['frame_idx']:06d}.jpg"
                        upload_file(minio_key, frame_path)
                        frame_path.unlink(missing_ok=True)

                        db_frame = Frame(
                            video_id=video.id,
                            frame_number=det["frame_idx"],
                            timestamp_s=det["timestamp_s"],
                            minio_key=minio_key,
                            width=det["width"],
                            height=det["height"],
                        )
                        session.add(db_frame)
                        session.flush()
                        frame_id = db_frame.id
                    else:
                        frame_id = existing_frame.id

                    # Map label to class
                    label = det["label"]
                    class_idx = 0
                    for ci, cn in enumerate(class_names):
                        if cn == label or prompts[ci] == label:
                            class_idx = ci
                            label = cn
                            break

                    ann = Annotation(
                        frame_id=frame_id,
                        job_id=job.id,
                        class_name=label,
                        class_index=class_idx,
                        polygon=det["polygon"],
                        bbox=det["bbox"],
                        confidence=det["score"],
                        status="pending",
                    )
                    session.add(ann)
                    all_annotations.append(ann)

                    live_dets.append({
                        "class": label,
                        "confidence": det["score"],
                        "track_id": det.get("track_id"),
                    })

                # Stream unique detections to UI
                if live_dets:
                    _publish_detection(job_id, video.filename, 0, live_dets)

                session.commit()

                # Track timing
                vid_elapsed = _time.perf_counter() - vid_start
                video_times.append(vid_elapsed)

                # Update progress
                _update_job(
                    session, job,
                    processed_frames=vid_idx + 1,
                    progress=(vid_idx + 1) / len(videos),
                )

                # Clean up video file to save disk
                video_path.unlink(missing_ok=True)

            # Final status
            _update_job(
                session, job,
                status="completed",
                total_frames=len(videos),
                processed_frames=len(videos),
                progress=1.0,
            )

            _publish_progress(job_id, len(videos), len(videos), "done")

            return {
                "status": "completed",
                "videos_processed": len(videos),
                "annotations_created": len(all_annotations),
            }

    except Exception as e:
        logger.exception("Video labeling pipeline failed")
        try:
            _update_job(session, job, status="failed", error_message=str(e)[:500])
        except Exception:
            pass
        return {"status": "failed", "error": str(e)}
    finally:
        session.close()
