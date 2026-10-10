"""Video-native labeling pipeline using SAM3.1 MLX.

Processes videos directly without frame extraction. Shares each frame's backbone
across prompts and tracks objects across sampled video frames.
Streams detections to Redis for live UI updates.
"""

import copy
import json
import logging
import math
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from labeler.errors import RetryableLabelingError, is_retryable
from labeler.pipeline import _update_job
from lib.config import settings
from lib.dataset_evidence import invalidate_current_export
from lib.db import Annotation, Frame, LabelingJob, SessionLocal, Video
from lib.storage import download_file, upload_file
from lib.video_timing import frame_timing, probe_frame_timing

logger = logging.getLogger(__name__)

# Module-level Redis connection pool — avoids creating a new connection per publish
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis

        _redis_client = redis.from_url(settings.redis_url)
    return _redis_client


def _publish_detection(job_id: str, video_name: str, frame_idx: int, detections: list[dict]):
    """Publish detections to Redis for live UI streaming."""
    try:
        r = _get_redis()
        r.publish(
            f"waldo:labeling:{job_id}",
            json.dumps(
                {
                    "type": "detection",
                    "video": video_name,
                    "frame_idx": frame_idx,
                    "detections": detections,
                }
            ),
        )
    except Exception:
        pass  # Non-critical — UI streaming is best-effort


def _publish_progress(
    job_id: str,
    videos_done: int,
    videos_total: int,
    video_name: str,
    avg_seconds: float = 0,
    eta_seconds: float = 0,
    annotations_so_far: int = 0,
):
    """Publish progress update with ETA to Redis."""
    try:
        r = _get_redis()
        r.publish(
            f"waldo:labeling:{job_id}",
            json.dumps(
                {
                    "type": "progress",
                    "videos_done": videos_done,
                    "videos_total": videos_total,
                    "current_video": video_name,
                    "progress": videos_done / max(1, videos_total),
                    "avg_seconds_per_video": round(avg_seconds, 1),
                    "eta_seconds": round(eta_seconds),
                    "annotations": annotations_so_far,
                }
            ),
        )
    except Exception:
        pass


_cached_predictor = None
_cached_resolution: int | None = None


def _get_predictor(threshold: float = 0.15, resolution: int = 1008):
    """Cached SAM3.1 MLX predictor — loaded once, reused across videos.

    Threshold is applied on every call so different jobs can use different
    confidence cutoffs without reloading the model. Resolution is baked into
    the processor, so a resolution change forces a reload.
    """
    global _cached_predictor, _cached_resolution
    if _cached_predictor is None or _cached_resolution != resolution:
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
        _cached_resolution = resolution
        logger.info("SAM3.1 MLX predictor loaded and cached (res=%d)", resolution)
    else:
        _cached_predictor.score_threshold = threshold
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
                if cv2.contourArea(largest) > 0:
                    eps = 0.001 * cv2.arcLength(largest, True)
                    approx = cv2.approxPolyDP(largest, eps, True)
                    if len(approx) >= 3:
                        pts = approx.reshape(-1, 2)
                        polygon = []
                        for px, py in pts:
                            polygon.append(float(px / W))
                            polygon.append(float(py / H))

        det_list.append(
            {
                "bbox": result.boxes[i].tolist() if i < len(result.boxes) else None,
                "score": float(result.scores[i]),
                "label": result.labels[i] if result.labels and i < len(result.labels) else prompts[0],
                "track_id": int(result.track_ids[i])
                if result.track_ids is not None and i < len(result.track_ids)
                else None,
                "polygon": polygon,
            }
        )
    return det_list


def process_video_native(
    video_path: str,
    prompts: list[str],
    threshold: float = 0.35,
    detect_every: int = 30,
    resolution: int = 1008,
    *,
    sample_fps: float | None = None,
) -> list[dict]:
    """Assess sampled source frames with fresh image features for each frame.

    A provided sample FPS determines the source-frame stride; otherwise the
    legacy explicit stride is used. Every sampled frame is returned, including
    negative frames, so callers can report the assessed timestamps. Time is
    taken from source presentation timestamps when available; otherwise each
    result explicitly identifies its frame-index/FPS approximation.
    """
    import mlx.core as mx
    from mlx_vlm.generate import wired_limit
    from mlx_vlm.models.sam3.generate import SimpleTracker
    from mlx_vlm.models.sam3_1.generate import _get_backbone_features

    from labeler.sam3_optimized import detect_with_backbone_fast as _detect_with_backbone

    if sample_fps is not None and (not math.isfinite(sample_fps) or sample_fps <= 0):
        raise ValueError("sample_fps must be a positive finite number")
    if detect_every < 1:
        raise ValueError("detect_every must be positive")
    predictor = _get_predictor(threshold, resolution)
    timings = probe_frame_timing(video_path)
    cap = cv2.VideoCapture(video_path)
    results = []
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")
        # OpenCV can estimate frame count from duration * FPS for VFR sources.
        total_frames = len(timings) if timings else int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if total_frames <= 0 or not math.isfinite(fps) or fps <= 0 or width <= 0 or height <= 0:
            raise ValueError(f"Invalid video metadata: {video_path}")
        stride = max(1, int(round(fps / sample_fps))) if sample_fps is not None else detect_every
        tracker = SimpleTracker()
        with wired_limit(predictor.model):
            for frame_index in range(total_frames):
                decoded, image = cap.read()
                if not decoded:
                    raise RuntimeError(
                        f"Decode stopped at source frame {frame_index} of {total_frames}; "
                        f"cannot verify complete source coverage: {video_path}"
                    )
                if frame_index % stride:
                    continue
                height, width = image.shape[:2]
                timestamp_s, duration_s, method = frame_timing(frame_index, fps, timings)
                frame = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                inputs = predictor.processor.preprocess_image(frame)
                pixels = mx.array(inputs["pixel_values"])
                backbone = _get_backbone_features(predictor.model, pixels)
                result = _detect_with_backbone(predictor, backbone, prompts, frame.size, threshold, encoder_cache={})
                result = tracker.update(result)
                results.append(
                    {
                        "frame_idx": frame_index,
                        "timestamp_s": timestamp_s,
                        "frame_duration_s": duration_s,
                        "timestamp_method": method,
                        "source_width": width,
                        "source_height": height,
                        "width": width,
                        "height": height,
                        "source_fps": fps,
                        "sampling_stride": stride,
                        "detections": _result_to_detections(result, width, height, prompts),
                    }
                )
            if cap.read()[0]:
                raise RuntimeError(
                    f"Decoded frames exceed the expected source frame count of {total_frames}; "
                    f"cannot verify complete source coverage: {video_path}"
                )
    finally:
        cap.release()
    logger.info(
        "Assessed %d sampled frames in %s; %d sightings",
        len(results),
        video_path,
        sum(len(result["detections"]) for result in results),
    )
    return results


def _run_playground_pytorch(
    video_id: str,
    prompts: list[str],
    threshold: float,
    frame_count: int,
    start_sec: float,
    duration_sec: float | None,
    sample_fps: float,
) -> dict:
    """PyTorch playground — Linux/Windows path.

    Uses `Sam3VideoModel` via `Sam3VideoInferenceSession` (the same engine the
    real Linux labeling pipeline uses) to detect-and-track across the sampled
    frames of the window. The session's per-object `obj_id` is returned as
    `track_id` so the UI's tracking summary works on Docker/CUDA too.
    """
    import base64
    import io
    import tempfile
    from pathlib import Path

    import torch
    from PIL import Image
    from transformers.models.sam3_video.modeling_sam3_video import Sam3VideoInferenceSession

    from labeler.sam3_engine import get_engine

    session_db = SessionLocal()
    try:
        video = session_db.query(Video).filter_by(id=video_id).one()
        minio_key = video.minio_key
    finally:
        session_db.close()

    with tempfile.TemporaryDirectory() as tmp:
        video_path = str(Path(tmp) / "video.mp4")
        download_file(minio_key, video_path)

        timings = probe_frame_timing(video_path)
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video {minio_key}")
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        video_duration_s = total_frames / fps if fps > 0 else 0.0

        contiguous = duration_sec is not None and duration_sec > 0
        if contiguous:
            start_idx = max(0, int(round(start_sec * fps)))
            end_idx = min(total_frames, int(round((start_sec + duration_sec) * fps)))
            if end_idx <= start_idx:
                end_idx = min(total_frames, start_idx + 1)
            stride = max(1, int(round(fps / max(0.1, sample_fps))))
            indices = (
                [i for i, timing in enumerate(timings) if start_sec <= timing.timestamp_s < start_sec + duration_sec][
                    ::stride
                ]
                if timings
                else list(range(start_idx, end_idx, stride))
            )
            # PyTorch path is slower than MLX — cap tighter to stay under the
            # synchronous HTTP timeout for the /label/preview endpoint.
            if len(indices) > 40:
                indices = indices[:40]
        else:
            frame_count = max(1, min(frame_count, 16))
            if total_frames < frame_count:
                indices = list(range(total_frames))
            else:
                start = int(total_frames * 0.05)
                end = int(total_frames * 0.95)
                step = max(1, (end - start) // max(1, frame_count - 1))
                indices = [start + i * step for i in range(frame_count)]
                indices = [min(i, total_frames - 1) for i in indices]

        pil_frames: list[Image.Image] = []
        frame_meta: list[dict] = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame_bgr = cap.read()
            if not ret:
                continue
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            H, W = frame_rgb.shape[:2]
            timestamp_s, duration_s, method = frame_timing(idx, fps, timings)
            pil_frames.append(Image.fromarray(frame_rgb))
            frame_meta.append(
                {
                    "frame_index": idx,
                    "timestamp_s": timestamp_s,
                    "frame_duration_s": duration_s,
                    "timestamp_method": method,
                }
            )
        cap.release()

    if not pil_frames:
        return {
            "video_id": video_id,
            "prompts": prompts,
            "threshold": threshold,
            "fps": fps,
            "width": W,
            "height": H,
            "total_frames": total_frames,
            "video_duration_s": video_duration_s,
            "start_sec": start_sec if contiguous else 0.0,
            "duration_sec": duration_sec if contiguous else 0.0,
            "sample_fps": sample_fps if contiguous else 0.0,
            "mode": "window" if contiguous else "sample",
            "frames": [],
            "total_detections": 0,
            "unique_track_count": 0,
        }

    engine = get_engine()
    first = pil_frames[0]

    processed = engine.processor(images=pil_frames, return_tensors="pt")
    pixel_values = processed["pixel_values"].to(engine.device, dtype=engine.torch_dtype)

    # One session per prompt — obj_ids are per-session, so namespace them by
    # prompt index to keep `track_id` globally unique across prompts.
    all_detections_by_frame: dict[int, list[dict]] = {i: [] for i in range(len(pil_frames))}

    for p_idx, prompt in enumerate(prompts):
        session = Sam3VideoInferenceSession(
            video=pixel_values,
            video_height=first.height,
            video_width=first.width,
            inference_device=engine.device,
            video_storage_device=engine.device,
            dtype=engine.torch_dtype,
        )
        engine.processor.add_text_prompt(session, prompt)

        for frame_idx in range(len(pil_frames)):
            output = engine.model(inference_session=session, frame_idx=frame_idx)
            obj_id_to_mask = getattr(output, "obj_id_to_mask", None) or {}
            obj_id_to_score = getattr(output, "obj_id_to_score", {}) or {}

            for obj_id, mask_tensor in obj_id_to_mask.items():
                score = obj_id_to_score.get(obj_id, 1.0)
                if isinstance(score, torch.Tensor):
                    score = float(score.item())
                else:
                    score = float(score)
                if score < threshold:
                    continue

                mask = mask_tensor.detach().cpu().float().numpy().squeeze()
                if mask.min() < -0.5 or mask.max() > 1.5:
                    mask = 1.0 / (1.0 + np.exp(-np.clip(mask, -50, 50)))
                mask_u8 = (mask > 0.5).astype(np.uint8) * 255
                if mask_u8.shape != (H, W):
                    mask_u8 = cv2.resize(mask_u8, (W, H), interpolation=cv2.INTER_NEAREST)
                if mask_u8.max() == 0:
                    continue

                ys, xs = np.where(mask_u8 > 0)
                x1, y1, x2, y2 = float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())

                polygon = None
                contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    largest = max(contours, key=cv2.contourArea)
                    if cv2.contourArea(largest) > 50:
                        eps = 0.001 * cv2.arcLength(largest, True)
                        approx = cv2.approxPolyDP(largest, eps, True)
                        if len(approx) >= 3:
                            pts = approx.reshape(-1, 2)
                            polygon = [coord for pt in pts for coord in (float(pt[0] / W), float(pt[1] / H))]

                global_track_id = int(obj_id) + p_idx * 10_000
                all_detections_by_frame[frame_idx].append(
                    {
                        "bbox": [x1, y1, x2, y2],
                        "score": score,
                        "label": prompt,
                        "track_id": global_track_id,
                        "polygon": polygon,
                    }
                )

        del session

    if engine.device == "cuda":
        torch.cuda.empty_cache()
    elif engine.device == "mps":
        torch.mps.empty_cache()

    frames_out: list[dict] = []
    for i, meta in enumerate(frame_meta):
        pil = pil_frames[i]
        thumb = pil
        if W > 960:
            new_h = int(H * 960 / W)
            thumb = pil.resize((960, new_h), Image.BILINEAR)
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=85)
        image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        frames_out.append(
            {
                "frame_index": meta["frame_index"],
                "timestamp_s": meta["timestamp_s"],
                "frame_duration_s": meta["frame_duration_s"],
                "timestamp_method": meta["timestamp_method"],
                "source_width": W,
                "source_height": H,
                "width": W,
                "height": H,
                "image_b64": image_b64,
                "detections": all_detections_by_frame.get(i, []),
            }
        )

    total_dets = sum(len(f["detections"]) for f in frames_out)
    unique_tracks = len({d["track_id"] for f in frames_out for d in f["detections"] if d.get("track_id") is not None})

    logger.info(
        "Playground(pytorch): video %s prompts=%s threshold=%.2f frames=%d dets=%d tracks=%d",
        video_id,
        prompts,
        threshold,
        len(frames_out),
        total_dets,
        unique_tracks,
    )

    return {
        "video_id": video_id,
        "prompts": prompts,
        "threshold": threshold,
        "fps": fps,
        "width": W,
        "height": H,
        "total_frames": total_frames,
        "video_duration_s": video_duration_s,
        "start_sec": start_sec if contiguous else 0.0,
        "duration_sec": duration_sec if contiguous else 0.0,
        "sample_fps": sample_fps if contiguous else 0.0,
        "mode": "window" if contiguous else "sample",
        "frames": frames_out,
        "total_detections": total_dets,
        "unique_track_count": unique_tracks,
    }


def run_playground(
    video_id: str,
    prompts: list[str],
    threshold: float = 0.35,
    frame_count: int = 8,
    resolution: int = 1008,
    start_sec: float = 0.0,
    duration_sec: float | None = None,
    sample_fps: float = 4.0,
) -> dict:
    """Test SAM3 prompts on a short contiguous window of one video.

    Platform routing:
    - **macOS (Darwin)** → MLX via mlx-vlm (`SimpleTracker`, backbone cache)
    - **Linux / Windows** → PyTorch via `Sam3VideoInferenceSession`

    Two modes:

    1. **Contiguous window** (when `duration_sec` is set): samples frames at
       `sample_fps` samples/sec across `[start_sec, start_sec + duration_sec]`
       and runs tracking across them so object IDs persist — critical for
       verifying SAM will dedupe objects during a real labeling job.

    2. **Legacy evenly-spaced** (when `duration_sec` is None): picks
       `frame_count` evenly-spaced frames across the whole video (MLX only).
    """
    import platform

    if not prompts:
        raise ValueError("prompts must be non-empty")

    if platform.system() != "Darwin":
        return _run_playground_pytorch(
            video_id=video_id,
            prompts=prompts,
            threshold=threshold,
            frame_count=frame_count,
            start_sec=start_sec,
            duration_sec=duration_sec,
            sample_fps=sample_fps,
        )

    import base64
    import io
    import tempfile
    from pathlib import Path

    import mlx.core as mx
    from mlx_vlm.generate import wired_limit
    from mlx_vlm.models.sam3.generate import SimpleTracker
    from mlx_vlm.models.sam3_1.generate import _get_backbone_features
    from PIL import Image

    from labeler.sam3_optimized import detect_with_backbone_fast as _detect_with_backbone

    session = SessionLocal()
    try:
        video = session.query(Video).filter_by(id=video_id).one()
        minio_key = video.minio_key
    finally:
        session.close()

    predictor = _get_predictor(threshold, resolution)

    with tempfile.TemporaryDirectory() as tmp:
        video_path = str(Path(tmp) / "video.mp4")
        download_file(minio_key, video_path)

        timings = probe_frame_timing(video_path)
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video {minio_key}")
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        video_duration_s = total_frames / fps if fps > 0 else 0.0

        contiguous = duration_sec is not None and duration_sec > 0
        tracker: SimpleTracker | None = SimpleTracker() if contiguous else None

        if contiguous:
            start_idx = max(0, int(round(start_sec * fps)))
            end_idx = min(total_frames, int(round((start_sec + duration_sec) * fps)))
            if end_idx <= start_idx:
                end_idx = min(total_frames, start_idx + 1)
            stride = max(1, int(round(fps / max(0.1, sample_fps))))
            indices = (
                [i for i, timing in enumerate(timings) if start_sec <= timing.timestamp_s < start_sec + duration_sec][
                    ::stride
                ]
                if timings
                else list(range(start_idx, end_idx, stride))
            )
            # Cap so we don't DOS the worker — at sample_fps=4, 16s ≈ 64 frames.
            if len(indices) > 120:
                indices = indices[:120]
        else:
            frame_count = max(1, min(frame_count, 32))
            if total_frames < frame_count:
                indices = list(range(total_frames))
            else:
                start = int(total_frames * 0.05)
                end = int(total_frames * 0.95)
                step = max(1, (end - start) // max(1, frame_count - 1))
                indices = [start + i * step for i in range(frame_count)]
                indices = [min(i, total_frames - 1) for i in indices]

        frames_out: list[dict] = []

        with wired_limit(predictor.model):
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame_bgr = cap.read()
                if not ret:
                    continue
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                H, W = frame_rgb.shape[:2]
                timestamp_s, duration_s, method = frame_timing(idx, fps, timings)
                frame_pil = Image.fromarray(frame_rgb)

                inputs = predictor.processor.preprocess_image(frame_pil)
                pixel_values = mx.array(inputs["pixel_values"])

                backbone = _get_backbone_features(predictor.model, pixel_values)
                result = _detect_with_backbone(
                    predictor,
                    backbone,
                    prompts,
                    frame_pil.size,
                    threshold,
                    encoder_cache={},
                )
                if tracker is not None:
                    result = tracker.update(result)

                dets = _result_to_detections(result, W, H, prompts)

                thumb = frame_pil
                if W > 960:
                    new_h = int(H * 960 / W)
                    thumb = frame_pil.resize((960, new_h), Image.BILINEAR)

                buf = io.BytesIO()
                thumb.save(buf, format="JPEG", quality=85)
                image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

                frames_out.append(
                    {
                        "frame_index": idx,
                        "timestamp_s": timestamp_s,
                        "frame_duration_s": duration_s,
                        "timestamp_method": method,
                        "source_width": W,
                        "source_height": H,
                        "width": W,
                        "height": H,
                        "image_b64": image_b64,
                        "detections": dets,
                    }
                )

        cap.release()

    total_dets = sum(len(f["detections"]) for f in frames_out)
    unique_tracks = (
        len({d["track_id"] for f in frames_out for d in f["detections"] if d.get("track_id") is not None})
        if contiguous
        else 0
    )
    logger.info(
        "Playground: video %s prompts=%s threshold=%.2f mode=%s frames=%d dets=%d tracks=%d",
        video_id,
        prompts,
        threshold,
        "window" if contiguous else "sample",
        len(frames_out),
        total_dets,
        unique_tracks,
    )

    return {
        "video_id": video_id,
        "prompts": prompts,
        "threshold": threshold,
        "fps": fps,
        "width": W,
        "height": H,
        "total_frames": total_frames,
        "video_duration_s": video_duration_s,
        "start_sec": start_sec if contiguous else 0.0,
        "duration_sec": duration_sec if contiguous else 0.0,
        "sample_fps": sample_fps if contiguous else 0.0,
        "mode": "window" if contiguous else "sample",
        "frames": frames_out,
        "total_detections": total_dets,
        "unique_track_count": unique_tracks,
    }


def _replace_native_observations(session, job, video, video_path, frame_results, tmpdir, prompt_to_class, class_names):
    """Replace one video's sightings in this run; caller commits only on success."""
    source_frames = session.query(Frame.id).filter_by(video_id=video.id)
    session.query(Annotation).filter(
        Annotation.job_id == job.id,
        Annotation.frame_id.in_(source_frames),
    ).delete(synchronize_session=False)
    observations = []
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Cannot reopen video: {video.id}")
        for result in frame_results:
            if not result["detections"]:
                continue
            frame_index = result["frame_idx"]
            key = f"frames/{job.id}/{video.id}_{frame_index:06d}.jpg"
            # Run-specific keys avoid conflating extraction ordinals with
            # source decode indices from other pipelines/jobs.
            frame = session.query(Frame).filter_by(video_id=video.id, minio_key=key).first()
            if frame is None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                decoded, image = cap.read()
                if not decoded:
                    raise RuntimeError(f"Cannot read evidence source frame {frame_index} in video {video.id}")
                image_path = tmpdir / f"frame_{video.id}_{frame_index}.jpg"
                if not cv2.imwrite(str(image_path), image):
                    raise OSError(f"Cannot write evidence frame: {image_path}")
                upload_file(key, image_path)
                image_path.unlink(missing_ok=True)
                frame = Frame(
                    video_id=video.id,
                    frame_number=frame_index,
                    timestamp_s=result["timestamp_s"],
                    minio_key=key,
                    width=result["width"],
                    height=result["height"],
                )
                session.add(frame)
                session.flush()
            live = []
            for detection in result["detections"]:
                label = prompt_to_class.get(detection["label"], detection["label"])
                if label not in class_names:
                    raise ValueError(f"Unknown detection label: {label}")
                box = detection.get("bbox")
                if box is None or len(box) != 4:
                    raise ValueError("Detection lacks a valid source box")
                x1, y1, x2, y2 = box
                width, height = result["width"], result["height"]
                if width <= 0 or height <= 0:
                    raise ValueError("Invalid evidence dimensions")
                track_id = detection.get("track_id")
                annotation = Annotation(
                    frame_id=frame.id,
                    job_id=job.id,
                    class_name=label,
                    class_index=class_names.index(label),
                    polygon=detection.get("polygon") or [],
                    bbox=[
                        float((x1 + x2) / 2 / width),
                        float((y1 + y2) / 2 / height),
                        float((x2 - x1) / width),
                        float((y2 - y1) / height),
                    ],
                    confidence=float(detection["score"]),
                    status="pending",
                    track_id=int(track_id) if track_id is not None else None,
                )
                session.add(annotation)
                observations.append(annotation)
                live.append({"class": label, "confidence": annotation.confidence, "track_id": track_id})
            _publish_detection(str(job.id), video.filename, frame_index, live)
        return observations
    finally:
        cap.release()


def run_video_labeling_pipeline(celery_task, job_id: str) -> dict:
    """Store every sampled sighting and report failed clips explicitly.

    Track identity is local to this job/video, not a physical-asset identity.
    Each clip records source PTS or its explicit frame-index/FPS approximation.
    Retries reuse completed clip transactions, including subsequent review edits.
    """
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).one()
        if getattr(job, "status", None) == "completed":
            summary = job.processing_summary or {}
            result = {"status": "completed", "processing_summary": summary}
            if isinstance(summary.get("videos"), list):
                entries = summary["videos"]
                completed = [entry for entry in entries if entry["status"] == "completed"]
                result.update(
                    videos_processed=len(completed),
                    videos_failed=sum(entry["status"] == "failed" for entry in entries),
                    annotations_created=sum(entry.get("observations", 0) for entry in completed),
                )
            return result
        class_prompts = job.class_prompts or [{"name": job.text_prompt, "prompt": job.text_prompt}]
        prompts, prompt_to_class, class_names = [], {}, []
        for config in class_prompts:
            name = config["name"]
            if name not in class_names:
                class_names.append(name)
            for alias in config.get("prompts") or [config.get("prompt", name)]:
                if alias not in prompt_to_class:
                    prompts.append(alias)
                    prompt_to_class[alias] = name
        threshold = getattr(job, "score_threshold", None)
        if threshold is None:
            threshold = settings.sam3_score_threshold
        sample_fps = getattr(job, "sample_fps", None)
        summary = (
            copy.deepcopy(job.processing_summary)
            if job.processing_summary
            else {
                "backend": "mlx-image-iou-tracker",
                "coverage": "sampled",
                "timestamp_method": "per_frame_source_pts_or_explicit_fps_approximation",
                "score_threshold": threshold,
                "requested_sample_fps": sample_fps,
                "decode_gap_assessment": "early_decode_stop_fails_clip",
                "videos": [],
            }
        )
        for key, value in (("score_threshold", threshold), ("requested_sample_fps", sample_fps)):
            if key in summary and summary[key] != value:
                raise ValueError(f"Retry configuration differs from recorded {key}; start a new labeling job")
        if job.project_id and not job.video_id:
            videos = session.query(Video).filter_by(project_id=job.project_id).all()
        elif job.video_id:
            videos = [session.query(Video).filter_by(id=job.video_id).one()]
        else:
            videos = []
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
                    raise ValueError(
                        "Existing observations lack a committed clip completion record; start a new labeling job"
                    )
        summary["videos"] = [completed[str(video.id)] for video in videos if str(video.id) in completed]
        videos_processed = len(summary["videos"])
        observations_count = sum(entry.get("observations", 0) for entry in summary["videos"])
        frames_processed = sum(entry.get("sampled_frames", 0) for entry in summary["videos"])
        _update_job(
            session,
            job,
            status="labeling",
            total_frames=frames_processed,
            processed_frames=frames_processed,
            progress=videos_processed / len(videos) if videos else 0.0,
            processing_summary=summary,
            error_message=None,
        )
        celery_task.update_state(state="LABELING")
        with tempfile.TemporaryDirectory() as directory:
            tmpdir = Path(directory)
            for video in videos:
                if str(video.id) in completed:
                    continue
                video_path = tmpdir / f"{video.id}_{Path(video.filename).name}"
                entry = {"video_id": str(video.id), "status": "failed"}
                try:
                    download_file(video.minio_key, video_path)
                    results = process_video_native(str(video_path), prompts, threshold=threshold, sample_fps=sample_fps)
                    invalidate_current_export(session, job.id)
                    annotations = _replace_native_observations(
                        session,
                        job,
                        video,
                        video_path,
                        results,
                        tmpdir,
                        prompt_to_class,
                        class_names,
                    )
                    entry.update(
                        {
                            "status": "completed",
                            "sampled_frames": len(results),
                            "observations": len(annotations),
                            "assessed_timestamps_s": [result["timestamp_s"] for result in results],
                            "assessed_source_frame_indices": [result["frame_idx"] for result in results],
                            "source_fps": results[0].get("source_fps") if results else None,
                            "sampling_stride": results[0].get("sampling_stride") if results else None,
                            "timestamp_method": results[0].get("timestamp_method") if results else None,
                        }
                    )
                    next_summary = {**summary, "videos": [*summary["videos"], entry]}
                    # Completion metadata and its observations must commit in
                    # one transaction; otherwise redelivery cannot reuse them.
                    _update_job(
                        session,
                        job,
                        processed_frames=frames_processed + len(results),
                        total_frames=frames_processed + len(results),
                        progress=(videos_processed + 1) / len(videos),
                        processing_summary=next_summary,
                        result_minio_key=None,
                    )
                    summary = next_summary
                    observations_count += len(annotations)
                    videos_processed += 1
                    frames_processed += len(results)
                except Exception as error:
                    session.rollback()
                    entry = {"video_id": str(video.id), "status": "failed", "error": str(error)}
                    logger.exception("Video %s failed during labeling/evidence storage", video.id)
                    if is_retryable(error):
                        entry["status"] = "retrying"
                        summary["videos"].append(entry)
                        _update_job(
                            session,
                            job,
                            status="retrying",
                            processing_summary=summary,
                            processed_frames=frames_processed,
                            total_frames=frames_processed,
                            error_message=str(error),
                        )
                        raise RetryableLabelingError(str(error)) from error
                finally:
                    video_path.unlink(missing_ok=True)
                if entry["status"] != "completed":
                    summary["videos"].append(entry)
                    _update_job(
                        session,
                        job,
                        processed_frames=frames_processed,
                        total_frames=frames_processed,
                        progress=videos_processed / len(videos),
                        processing_summary=dict(summary),
                    )
                _publish_progress(
                    job_id, videos_processed, len(videos), video.filename, annotations_so_far=observations_count
                )
        failures = [entry for entry in summary["videos"] if entry["status"] == "failed"]
        status = "completed" if videos and not failures else "partial" if videos_processed else "failed"
        error_message = "; ".join(f"{entry['video_id']}: {entry['error']}" for entry in failures) or (
            "No videos to process" if not videos else None
        )
        _update_job(
            session,
            job,
            status=status,
            error_message=error_message,
            processing_summary=dict(summary),
            processed_frames=frames_processed,
            total_frames=frames_processed,
            progress=videos_processed / len(videos) if videos else 0.0,
        )
        return {
            "status": status,
            "videos_processed": videos_processed,
            "videos_failed": len(failures),
            "annotations_created": observations_count,
            "processing_summary": summary,
        }
    except Exception as error:
        session.rollback()
        logger.exception("Video labeling pipeline failed")
        retryable = is_retryable(error)
        try:
            _update_job(session, job, status="retrying" if retryable else "failed", error_message=str(error))
        except Exception:
            pass
        if retryable:
            if isinstance(error, RetryableLabelingError):
                raise
            raise RetryableLabelingError(str(error)) from error
        return {"status": "failed", "error": str(error)}
    finally:
        session.close()
