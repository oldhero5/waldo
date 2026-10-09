import copy
import importlib.util
import logging
import platform
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import UUID

from celery import Celery
from redis.exceptions import RedisError

from labeler.errors import RetryableLabelingError
from lib.config import settings

app = Celery("waldo", broker=settings.redis_url, backend=settings.redis_url)
app.Task.resultrepr_maxsize = 0

# Redis defaults to redelivery after one hour, before long jobs finish. This
# window exceeds configured limits, but solo workers do not enforce those limits.
# Every worker sharing this broker must use the same value; crash recovery can
# wait this long when the worker cannot requeue its unacknowledged messages.
_VISIBILITY_TIMEOUT = 25 * 3600

app.conf.update(
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    # Reliability: lost workers (OOM, SIGKILL) must re-queue the task, not drop it silently.
    task_reject_on_worker_lost=True,
    # Per-task defaults — decorators below opt individual tasks into retries and bound runtimes.
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": _VISIBILITY_TIMEOUT},
    result_backend_transport_options={"visibility_timeout": _VISIBILITY_TIMEOUT},
    visibility_timeout=_VISIBILITY_TIMEOUT,
    result_expires=24 * 3600,
)

# Shared retry config applied to long-running jobs that must survive transient failures
# (Redis hiccup, DB blip, MinIO timeout). Configured time limits require a supporting
# worker pool; the current solo workers do not enforce them.
_RETRY_OPTS = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_backoff_max": 600,  # cap backoff at 10 minutes
    "retry_jitter": True,
    "max_retries": 3,
}
_INFERENCE_RETRY_OPTS = {**_RETRY_OPTS, "autoretry_for": (RedisError,)}


def _native_video_available() -> bool:
    """Native MLX requires Apple Silicon plus its installed runtime packages."""
    return (
        platform.system() == "Darwin"
        and platform.machine() == "arm64"
        and importlib.util.find_spec("mlx") is not None
        and importlib.util.find_spec("mlx_vlm") is not None
    )


@app.task(
    name="waldo.label_video",
    bind=True,
    **{**_RETRY_OPTS, "autoretry_for": (RetryableLabelingError,)},
    soft_time_limit=6 * 3600,
    time_limit=6 * 3600 + 300,
)
def label_video(self, job_id: str, merge_into: str | None = None) -> dict:
    try:
        if _native_video_available():
            from labeler.video_labeler import run_video_labeling_pipeline

            result = run_video_labeling_pipeline(self, job_id)
        else:
            from labeler.text_labeler import run_labeling_pipeline

            result = run_labeling_pipeline(self, job_id)
    except RetryableLabelingError as error:
        _record_labeling_retry(self, job_id, error)
        raise

    if merge_into and result.get("status") == "completed":
        from labeler.errors import is_retryable
        from lib.db import SessionLocal

        session = SessionLocal()
        try:
            _merge_completed_labeling_job(session, job_id, merge_into)
            session.commit()
        except Exception as error:
            session.rollback()
            if is_retryable(error):
                retryable = RetryableLabelingError(str(error))
                _record_labeling_retry(self, job_id, retryable)
                raise retryable from error
            raise
        finally:
            session.close()

    return result


def _record_labeling_retry(task, job_id, error):
    from labeler.pipeline import _update_job
    from lib.db import LabelingJob, SessionLocal

    session = None
    try:
        session = SessionLocal()
        job = session.query(LabelingJob).filter_by(id=job_id).one()
        exhausted = task.request.retries >= task.max_retries
        summary = copy.deepcopy(job.processing_summary)
        if exhausted and summary:
            for video in summary.get("videos", []):
                if video.get("status") == "retrying":
                    video["status"] = "failed"
        _update_job(
            session,
            job,
            status="failed" if exhausted else "retrying",
            processing_summary=summary,
            error_message=str(error),
        )
    except Exception:
        # A database outage must not mask the original retryable failure.
        if session is not None:
            session.rollback()
    finally:
        if session is not None:
            session.close()


def _merge_completed_labeling_job(session, child_id, master_id):
    """Transfer evidence atomically while retaining its originating run metadata."""
    from lib.db import Annotation, Frame, LabelingJob

    child = session.query(LabelingJob).filter_by(id=UUID(str(child_id))).one()
    # Serialize concurrent add-class merges so track IDs and run metadata cannot
    # be allocated from the same stale parent snapshot.
    master = session.query(LabelingJob).filter_by(id=UUID(str(master_id))).with_for_update().one()
    if child.id == master.id or child.status != "completed":
        raise ValueError("Only a distinct completed labeling run can be merged")
    child_project = child.project_id or (child.video.project_id if child.video else None)
    master_project = master.project_id or (master.video.project_id if master.video else None)
    if child_project is None or child_project != master_project:
        raise ValueError("Merged labeling runs must belong to the same project")
    metadata = copy.deepcopy(master.processing_summary or {})
    merged_runs = metadata.setdefault("merged_runs", [])
    already_merged = any(run.get("job_id") == str(child.id) for run in merged_runs)
    if already_merged:
        # A redelivery can rerun the child; its evidence is already in the
        # parent. Discard only these duplicate child-run observations.
        session.query(Annotation).filter_by(job_id=child.id).delete(synchronize_session=False)
    else:
        parents = session.query(Annotation, Frame.video_id).join(Frame).filter(Annotation.job_id == master.id).all()
        children = session.query(Annotation, Frame.video_id).join(Frame).filter(Annotation.job_id == child.id).all()
        next_tracks = {}
        classes = {annotation.class_name: annotation.class_index for annotation, _ in parents}
        for annotation, video_id in parents:
            if annotation.track_id is not None:
                next_tracks[video_id] = max(next_tracks.get(video_id, 0), annotation.track_id)
        remap = {}
        for annotation, video_id in children:
            if annotation.track_id is not None:
                identity = (video_id, annotation.track_id)
                if identity not in remap:
                    next_tracks[video_id] = next_tracks.get(video_id, 0) + 1
                    remap[identity] = next_tracks[video_id]
                annotation.track_id = remap[identity]
            if annotation.class_name not in classes:
                classes[annotation.class_name] = max(classes.values(), default=-1) + 1
            annotation.class_index = classes[annotation.class_name]
            annotation.job_id = master.id
        merged_runs.append(
            {
                "job_id": str(child.id),
                "score_threshold": child.score_threshold,
                "sample_fps": child.sample_fps,
                "task_type": child.task_type,
                "class_prompts": copy.deepcopy(child.class_prompts),
                "processing_summary": copy.deepcopy(child.processing_summary),
                "track_remap": [
                    {"video_id": str(video_id), "source_track_id": old_id, "merged_track_id": new_id}
                    for (video_id, old_id), new_id in remap.items()
                ],
            }
        )
        master.result_minio_key = None
    master.processing_summary = metadata
    child.processing_summary = {**(child.processing_summary or {}), "merged_into": str(master.id)}


@app.task(
    name="waldo.label_video_exemplar",
    bind=True,
    **_RETRY_OPTS,
    soft_time_limit=6 * 3600,
    time_limit=6 * 3600 + 300,
)
def label_video_exemplar(self, job_id: str) -> dict:
    from labeler.exemplar_labeler import run_exemplar_pipeline

    return run_exemplar_pipeline(self, job_id)


@app.task(name="waldo.label_playground")
def label_playground(
    video_id: str,
    prompts: list[str],
    threshold: float = 0.35,
    frame_count: int = 8,
    start_sec: float = 0.0,
    duration_sec: float | None = None,
    sample_fps: float = 4.0,
) -> dict:
    """Ephemeral prompt test — runs SAM3.1 on a short window of the video.

    When `duration_sec` is set, processes a contiguous range so SimpleTracker
    can assign consistent track_ids across the sampled frames. Otherwise,
    falls back to the legacy evenly-spaced sampler. Returns base64 JPEGs +
    detections with track_ids. Nothing is persisted.
    """
    from labeler.video_labeler import run_playground
    from lib.preview import normalize_preview_result

    return normalize_preview_result(
        run_playground(
            video_id,
            prompts,
            threshold=threshold,
            frame_count=frame_count,
            start_sec=start_sec,
            duration_sec=duration_sec,
            sample_fps=sample_fps,
        )
    )


@app.task(
    name="waldo.train_model",
    bind=True,
    queue="training",
    **_RETRY_OPTS,
    soft_time_limit=24 * 3600,
    time_limit=24 * 3600 + 300,
)
def train_model(self, run_id: str) -> dict:
    from trainer.train_manager import run_training

    return run_training(self, run_id)


@app.task(name="waldo.export_model", bind=True, **_RETRY_OPTS, soft_time_limit=30 * 60, time_limit=35 * 60)
def export_model_task(self, model_id: str, fmt: str) -> dict:
    from trainer.exporter import export_model

    key = export_model(model_id, fmt)
    return {"model_id": model_id, "format": fmt, "export_key": key}


def _predict_video_from_file(self, video_path: str, conf: float, session_id: str, model_id: str | None = None) -> dict:
    """Run a YOLO tracker over a video and stream per-frame detections to Redis.

    Per-frame publishes happen at 30+ fps for HD video — JSON serialization
    showed up in profiles. The pubsub payloads use msgpack; `app/ws.py`
    detects the binary frame, unpacks, and re-serializes JSON to WS clients
    (so clients see no change in payload format).
    """
    from dataclasses import asdict

    from lib.redis_client import get_redis
    from lib.redis_serde import pack
    from lib.video_tracker import VideoTracker

    client = get_redis()
    channel = f"waldo:predict:frames:{session_id}"

    def on_frame(frame_result):
        from dataclasses import asdict

        payload = {
            "session_id": session_id,
            "frame_index": frame_result.frame_index,
            "timestamp_s": frame_result.timestamp_s,
            "source_width": frame_result.source_width,
            "source_height": frame_result.source_height,
            "frame_duration_s": frame_result.frame_duration_s,
            "timestamp_method": frame_result.timestamp_method,
            "detections": [asdict(d) for d in frame_result.detections],
            "status": "processing",
        }
        client.publish(channel, pack(payload))

    if model_id is not None:
        from lib.inference_engine import get_pool

        tracker = VideoTracker(conf=conf, engine=get_pool().get_model(model_id))
    else:
        tracker = VideoTracker(conf=conf)
    results = tracker.track_video(video_path, on_frame=on_frame)

    return {
        "session_id": session_id,
        "status": "completed",
        "total_frames": len(results),
        "model_id": model_id,
        "frames": [asdict(frame) for frame in results],
    }


def _compare_models_from_file(
    self,
    session_id: str,
    file_path: str,
    is_video: bool,
    model_a_id: str,  # model UUID or "sam3.1"
    model_b_id: str,
    conf: float,
    sam_prompts: list[str] | None = None,
) -> dict:
    """Run two models on the same file and store results in Redis.

    Publishes progress to waldo:compare:{session_id} channel.
    Final results stored in waldo:compare:result:{session_id} for 1 hour.
    """
    import json
    import time
    from dataclasses import asdict

    from lib.redis_client import get_redis

    client = get_redis()
    channel = f"waldo:compare:{session_id}"

    def publish(data):
        client.publish(channel, json.dumps(data))

    def run_yolo_image(model_id):
        import cv2

        from lib.inference_engine import get_pool

        pool = get_pool()
        engine = pool.get_model(model_id)
        image = cv2.imread(file_path)
        dets = engine.predict_image(image, conf=conf)
        return [asdict(d) for d in dets], None

    def run_yolo_video(model_id):
        from lib.inference_engine import get_pool
        from lib.video_tracker import VideoTracker

        pool = get_pool()
        engine = pool.get_model(model_id)
        tracker = VideoTracker(conf=conf, engine=engine)
        frames = tracker.track_video(file_path)
        frame_dicts = []
        all_dets = []
        for fr in frames:
            fd = {
                "frame_index": fr.frame_index,
                "timestamp_s": fr.timestamp_s,
                "source_width": fr.source_width,
                "source_height": fr.source_height,
                "frame_duration_s": fr.frame_duration_s,
                "timestamp_method": fr.timestamp_method,
                "detections": [asdict(d) for d in fr.detections],
            }
            frame_dicts.append(fd)
            all_dets.extend([asdict(d) for d in fr.detections])
        return all_dets, frame_dicts

    def run_sam_image():
        import cv2
        import mlx.core as mx
        from mlx_vlm.models.sam3_1.generate import _get_backbone_features
        from PIL import Image

        from labeler.sam3_optimized import detect_with_backbone_fast
        from labeler.video_labeler import _get_predictor

        predictor = _get_predictor(threshold=conf)
        image = cv2.imread(file_path)
        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        inputs = predictor.processor.preprocess_image(pil)
        pixel_values = mx.array(inputs["pixel_values"])
        backbone = _get_backbone_features(predictor.model, pixel_values)
        result = detect_with_backbone_fast(
            predictor, backbone, sam_prompts or [], image_size=pil.size, threshold=conf, encoder_cache={}
        )
        h, w = image.shape[:2]
        dets = []
        for i in range(len(result.scores)):
            bbox = result.boxes[i].tolist() if i < len(result.boxes) else [0, 0, 0, 0]
            label = (
                result.labels[i]
                if result.labels and i < len(result.labels)
                else (sam_prompts[0] if sam_prompts else "object")
            )
            dets.append(
                {
                    "class_name": label,
                    "class_index": 0,
                    "confidence": float(result.scores[i]),
                    "bbox": [float(x) for x in bbox],
                    "track_id": None,
                    "mask": None,
                }
            )
        return dets, None

    def run_sam_video():
        from labeler.video_labeler import process_video_native

        frame_dicts = []
        all_dets = []
        prompts = sam_prompts or []
        for result in process_video_native(file_path, prompts, threshold=conf, detect_every=15):
            fd = {
                "frame_index": result["frame_idx"],
                "timestamp_s": result["timestamp_s"],
                "source_width": result["width"],
                "source_height": result["height"],
                "frame_duration_s": result.get("frame_duration_s"),
                "timestamp_method": result.get("timestamp_method"),
                "detections": [],
            }
            for detection in result["detections"]:
                polygon = detection.get("polygon")
                det = {
                    "class_name": detection.get("label", ""),
                    "class_index": 0,
                    "confidence": detection.get("score", 0),
                    "bbox": detection.get("bbox", [0, 0, 0, 0]),
                    "track_id": detection.get("track_id"),
                    "mask": [
                        [polygon[i] * result["width"], polygon[i + 1] * result["height"]]
                        for i in range(0, len(polygon), 2)
                    ]
                    if polygon
                    else None,
                }
                fd["detections"].append(det)
                all_dets.append(det)
            frame_dicts.append(fd)
        return all_dets, frame_dicts

    results = {}
    for side, model_id in [("a", model_a_id), ("b", model_b_id)]:
        label = "SAM 3.1" if model_id == "sam3.1" else model_id
        publish({"status": "running", "side": side, "model": label, "session_id": session_id})

        t0 = time.perf_counter()
        try:
            if model_id == "sam3.1":
                dets, frames = run_sam_video() if is_video else run_sam_image()
            else:
                dets, frames = run_yolo_video(model_id) if is_video else run_yolo_image(model_id)
            latency = (time.perf_counter() - t0) * 1000
            results[side] = {"dets": dets, "frames": frames, "latency": latency, "error": None}
        except Exception as e:
            latency = (time.perf_counter() - t0) * 1000
            results[side] = {"dets": [], "frames": None, "latency": latency, "error": str(e) or type(e).__name__}

        publish({"status": "done_side", "side": side, "session_id": session_id})

    all_failed = all(result["error"] is not None for result in results.values())
    return {
        "session_id": session_id,
        "status": "failed" if all_failed else "completed",
        "results": results,
        "error": "Both comparison models failed" if all_failed else None,
    }


@contextmanager
def _inference_input(input_key: str):
    """Resolve a server-generated ephemeral object on the executing worker."""
    from lib.storage import download_file

    parts = _validate_inference_key(input_key)
    with tempfile.TemporaryDirectory(prefix="waldo_inference_") as directory:
        path = Path(directory) / parts[3]
        download_file(input_key, path)
        yield str(path)


def _validate_inference_key(input_key: str) -> tuple[str, ...]:
    parts = PurePosixPath(input_key).parts
    if len(parts) != 4 or parts[0] != "inference" or not parts[3].startswith("input."):
        raise ValueError("Expected an inference object key")
    UUID(parts[1])
    UUID(parts[2])
    return parts


def _run_inference_input(kind: str, session_id: str, input_key: str, infer) -> dict:
    from lib.inference_results import get_inference_result, save_inference_result
    from lib.storage import delete_object

    cached = get_inference_result(kind, session_id)
    if cached and cached.get("status") in {"completed", "failed"}:
        return cached
    valid_input = False
    try:
        _validate_inference_key(input_key)
        valid_input = True
        with _inference_input(input_key) as path:
            result = infer(path)
        result = {**(cached or {}), **result, "session_id": session_id, "status": result.get("status", "completed")}
    except RedisError:
        raise
    except Exception as error:
        result = {
            **(cached or {}),
            "session_id": session_id,
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }
        if kind == "compare":
            result["results"] = {
                side: {"dets": [], "frames": None, "latency": 0, "error": result["error"]} for side in ("a", "b")
            }
    # If Redis persistence fails, retain the input for redelivery/lifecycle expiry.
    save_inference_result(kind, session_id, result)
    if valid_input:
        try:
            delete_object(input_key)
        except Exception:
            logging.getLogger(__name__).warning("Failed to remove ephemeral inference input", exc_info=True)
    return result


@app.task(name="waldo.predict_video", bind=True, **_INFERENCE_RETRY_OPTS)
def predict_video_task(self, input_key: str, conf: float, session_id: str, model_id: str | None = None) -> dict:
    return _run_inference_input(
        "predict",
        session_id,
        input_key,
        lambda path: _predict_video_from_file(self, path, conf, session_id, model_id=model_id),
    )


@app.task(name="waldo.compare_models", bind=True, **_INFERENCE_RETRY_OPTS)
def compare_models_task(
    self,
    session_id: str,
    input_key: str,
    is_video: bool,
    model_a_id: str,
    model_b_id: str,
    conf: float,
    sam_prompts: list[str] | None = None,
) -> dict:
    return _run_inference_input(
        "compare",
        session_id,
        input_key,
        lambda path: _compare_models_from_file(
            self, session_id, path, is_video, model_a_id, model_b_id, conf, sam_prompts
        ),
    )


def _sam_image_result(file_path: str, prompts: list[str], conf: float) -> dict:
    import cv2
    import mlx.core as mx
    import numpy as np
    from mlx_vlm.models.sam3_1.generate import _get_backbone_features
    from PIL import Image

    from labeler.sam3_optimized import detect_with_backbone_fast
    from labeler.video_labeler import _get_predictor

    image = cv2.imread(file_path)
    if image is None:
        raise ValueError("Invalid image file")
    h, w = image.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    predictor = _get_predictor(threshold=conf)
    inputs = predictor.processor.preprocess_image(pil)
    backbone = _get_backbone_features(predictor.model, mx.array(inputs["pixel_values"]))
    result = detect_with_backbone_fast(
        predictor, backbone, prompts, image_size=pil.size, threshold=conf, encoder_cache={}
    )
    detections = []
    for index, score in enumerate(result.scores):
        label = result.labels[index] if result.labels and index < len(result.labels) else prompts[0]
        polygon = None
        if index < len(result.masks):
            mask = (result.masks[index] > 0.5).astype(np.uint8) * 255
            if mask.shape != (h, w):
                mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                largest = max(contours, key=cv2.contourArea)
                if cv2.contourArea(largest) > 50:
                    approx = cv2.approxPolyDP(largest, 0.001 * cv2.arcLength(largest, True), True)
                    if len(approx) >= 3:
                        polygon = [[float(x), float(y)] for x, y in approx.reshape(-1, 2)]
        detections.append(
            {
                "class_name": label,
                "class_index": prompts.index(label) if label in prompts else 0,
                "confidence": float(score),
                "bbox": [float(x) for x in result.boxes[index]] if index < len(result.boxes) else [0, 0, 0, 0],
                "track_id": None,
                "mask": polygon,
            }
        )
    return {"detections": detections, "count": len(detections), "model_id": "sam3.1", "input_resolution": f"{w}x{h}"}


def _sam_video_result(file_path: str, prompts: list[str], conf: float) -> dict:
    from labeler.video_labeler import process_video_native

    frames = []
    for frame in process_video_native(file_path, prompts, threshold=conf, detect_every=15):
        detections = []
        w, h = frame["width"], frame["height"]
        for detection in frame["detections"]:
            polygon = detection.get("polygon")
            label = detection.get("label", prompts[0])
            detections.append(
                {
                    "class_name": label,
                    "class_index": prompts.index(label) if label in prompts else 0,
                    "confidence": detection.get("score", 0),
                    "bbox": detection.get("bbox") or [0, 0, 0, 0],
                    "track_id": detection.get("track_id"),
                    "mask": [[polygon[i] * w, polygon[i + 1] * h] for i in range(0, len(polygon), 2)]
                    if polygon and len(polygon) >= 6
                    else None,
                }
            )
        frames.append(
            {
                "frame_index": frame["frame_idx"],
                "timestamp_s": frame["timestamp_s"],
                "detections": detections,
                "source_width": w,
                "source_height": h,
                "frame_duration_s": frame.get("frame_duration_s"),
                "timestamp_method": frame.get("timestamp_method"),
            }
        )
    resolution = f"{frames[0]['source_width']}x{frames[0]['source_height']}" if frames else None
    return {"frames": frames, "total_frames": len(frames), "model_id": "sam3.1", "input_resolution": resolution}


@app.task(name="waldo.predict_sam", soft_time_limit=300, time_limit=330, **_INFERENCE_RETRY_OPTS)
def predict_sam_task(input_key: str, is_video: bool, prompts: list[str], conf: float) -> dict:
    def infer(path):
        if not _native_video_available():
            raise RuntimeError("SAM 3.1 requires a native Apple Silicon worker with MLX installed")
        return _sam_video_result(path, prompts, conf) if is_video else _sam_image_result(path, prompts, conf)

    result = _run_inference_input("sam", PurePosixPath(input_key).parts[2], input_key, infer)
    if result["status"] == "failed":
        raise RuntimeError(result["error"])
    return result
