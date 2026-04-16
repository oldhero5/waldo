from celery import Celery

from lib.config import settings

app = Celery("waldo", broker=settings.redis_url, backend=settings.redis_url)

app.conf.update(
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
)


@app.task(name="waldo.label_video", bind=True)
def label_video(self, job_id: str, merge_into: str | None = None) -> dict:
    # Use MLX video pipeline for project-based jobs (faster, supports SAM3.1)
    # Fall back to PyTorch text pipeline for single-video frame-extraction jobs
    from lib.db import LabelingJob, SessionLocal

    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        use_video_pipeline = job and job.project_id is not None
    finally:
        session.close()

    if use_video_pipeline:
        from labeler.video_labeler import run_video_labeling_pipeline

        result = run_video_labeling_pipeline(self, job_id)
    else:
        from labeler.text_labeler import run_labeling_pipeline

        result = run_labeling_pipeline(self, job_id)

    # If merge_into is set, move annotations to the master job and delete child
    if merge_into and result.get("status") == "completed":
        import logging

        logger = logging.getLogger(__name__)
        from lib.db import SessionLocal

        session = SessionLocal()
        try:
            from sqlalchemy import text

            # Both operations in a single transaction — either both succeed or neither does
            session.execute(
                text("UPDATE annotations SET job_id = :master WHERE job_id = :child"),
                {"master": merge_into, "child": job_id},
            )
            session.execute(
                text("DELETE FROM labeling_jobs WHERE id = :id"),
                {"id": job_id},
            )
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to merge job %s into %s: %s", job_id, merge_into, e)
        finally:
            session.close()

    return result


@app.task(name="waldo.label_video_exemplar", bind=True)
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

    return run_playground(
        video_id,
        prompts,
        threshold=threshold,
        frame_count=frame_count,
        start_sec=start_sec,
        duration_sec=duration_sec,
        sample_fps=sample_fps,
    )


@app.task(name="waldo.train_model", bind=True, queue="training")
def train_model(self, run_id: str) -> dict:
    from trainer.train_manager import run_training

    return run_training(self, run_id)


@app.task(name="waldo.export_model", bind=True)
def export_model_task(self, model_id: str, fmt: str) -> dict:
    from trainer.exporter import export_model

    key = export_model(model_id, fmt)
    return {"model_id": model_id, "format": fmt, "export_key": key}


@app.task(name="waldo.predict_video", bind=True)
def predict_video_task(self, video_path: str, conf: float, session_id: str) -> dict:
    import json

    import redis

    from lib.config import settings
    from lib.video_tracker import VideoTracker

    client = redis.Redis.from_url(settings.redis_url)
    channel = f"waldo:predict:frames:{session_id}"

    def on_frame(frame_result):
        from dataclasses import asdict

        payload = {
            "session_id": session_id,
            "frame_index": frame_result.frame_index,
            "timestamp_s": frame_result.timestamp_s,
            "detections": [asdict(d) for d in frame_result.detections],
            "status": "processing",
        }
        client.publish(channel, json.dumps(payload))

    tracker = VideoTracker(conf=conf)
    results = tracker.track_video(video_path, on_frame=on_frame)

    # Publish completion
    client.publish(
        channel,
        json.dumps(
            {
                "session_id": session_id,
                "status": "completed",
                "total_frames": len(results),
            }
        ),
    )

    return {"session_id": session_id, "total_frames": len(results)}


@app.task(name="waldo.compare_models", bind=True)
def compare_models_task(
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

    import redis

    from lib.config import settings

    client = redis.Redis.from_url(settings.redis_url)
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
        pool.get_model(model_id)  # ensure loaded
        tracker = VideoTracker(conf=conf)
        frames = tracker.track_video(file_path)
        frame_dicts = []
        all_dets = []
        for fr in frames:
            fd = {
                "frame_index": fr.frame_index,
                "timestamp_s": fr.timestamp_s,
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
        import cv2
        import mlx.core as mx
        from mlx_vlm.generate import wired_limit
        from mlx_vlm.models.sam3.generate import SimpleTracker
        from mlx_vlm.models.sam3_1.generate import _get_backbone_features
        from PIL import Image

        from labeler.sam3_optimized import detect_with_backbone_fast
        from labeler.video_labeler import _get_predictor, _result_to_detections

        predictor = _get_predictor(threshold=conf)
        cap = cv2.VideoCapture(file_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        tracker = SimpleTracker()
        frame_dicts = []
        all_dets = []
        prompts = sam_prompts or []
        with wired_limit(predictor.model):
            for fi in range(total):
                ret, frame_bgr = cap.read()
                if not ret:
                    break
                if fi % 15 != 0:
                    continue
                frame_pil = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
                inputs = predictor.processor.preprocess_image(frame_pil)
                pixel_values = mx.array(inputs["pixel_values"])
                backbone = _get_backbone_features(predictor.model, pixel_values)
                result = detect_with_backbone_fast(
                    predictor, backbone, prompts, image_size=frame_pil.size, threshold=conf, encoder_cache={}
                )
                result = tracker.update(result)
                det_list = _result_to_detections(result, W, H, prompts)
                fd = {"frame_index": fi, "timestamp_s": fi / fps, "detections": []}
                for d in det_list:
                    det = {
                        "class_name": d.get("label", ""),
                        "class_index": 0,
                        "confidence": d.get("score", 0),
                        "bbox": d.get("bbox", [0, 0, 0, 0]),
                        "track_id": d.get("track_id"),
                        "mask": None,
                    }
                    fd["detections"].append(det)
                    all_dets.append(det)
                if fd["detections"]:
                    frame_dicts.append(fd)
        cap.release()
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
            results[side] = {"dets": [], "frames": None, "latency": latency, "error": str(e)}

        publish({"status": "done_side", "side": side, "session_id": session_id})

    # Store full results in Redis (TTL 1 hour)
    client.setex(f"waldo:compare:result:{session_id}", 3600, json.dumps(results))

    publish({"status": "completed", "session_id": session_id})
    return {"session_id": session_id, "status": "completed"}
