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
    from labeler.text_labeler import run_labeling_pipeline

    result = run_labeling_pipeline(self, job_id)

    # If merge_into is set, move annotations to the master job and delete child
    if merge_into and result.get("status") == "completed":
        from lib.db import SessionLocal
        session = SessionLocal()
        try:
            # Move annotations from child to master
            from sqlalchemy import text
            session.execute(
                text("UPDATE annotations SET job_id = :master WHERE job_id = :child"),
                {"master": merge_into, "child": job_id},
            )
            # Delete the child job
            session.execute(
                text("DELETE FROM labeling_jobs WHERE id = :id"),
                {"id": job_id},
            )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    return result


@app.task(name="waldo.label_video_exemplar", bind=True)
def label_video_exemplar(self, job_id: str) -> dict:
    from labeler.exemplar_labeler import run_exemplar_pipeline

    return run_exemplar_pipeline(self, job_id)


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
    client.publish(channel, json.dumps({
        "session_id": session_id,
        "status": "completed",
        "total_frames": len(results),
    }))

    return {"session_id": session_id, "total_frames": len(results)}
