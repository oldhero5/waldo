"""Platform integration blocks — connect workflows to datasets, models, training, and deployment."""
from typing import Any

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class DatasetInputBlock(BlockBase):
    name = "dataset_input"
    display_name = "Dataset"
    description = "Load images from a labeled dataset for batch processing."
    category = "platform"
    input_ports = []
    output_ports = [
        Port("image", "image", "First image from dataset"),
        Port("count", "number", "Number of images available"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        import cv2

        from lib.db import Frame, LabelingJob, SessionLocal
        from lib.storage import download_file

        dataset_id = self.config.get("dataset_id", "")
        sample_count = self.config.get("sample_count", 1)
        if not dataset_id:
            raise ValueError("No dataset_id configured. Select a dataset in block settings.")

        session = SessionLocal()
        try:
            job = session.query(LabelingJob).filter_by(id=dataset_id).first()
            if not job:
                raise ValueError(f"Dataset {dataset_id} not found")

            frames = session.query(Frame).filter(
                Frame.id.in_(
                    session.query(Frame.id).join(LabelingJob, Frame.video_id == LabelingJob.video_id)
                    .filter(LabelingJob.id == dataset_id)
                )
            ).limit(sample_count).all()

            if not frames:
                raise ValueError("No frames found in dataset")

            # Download and decode the first frame
            import tempfile
            from pathlib import Path

            tmp = Path(tempfile.mkdtemp())
            img_path = tmp / "frame.jpg"
            download_file(frames[0].minio_key, img_path)
            image = cv2.imread(str(img_path))

            return BlockResult(
                outputs={"image": image, "count": len(frames)},
                metadata={"dataset": job.text_prompt or dataset_id, "frames": len(frames)},
            )
        finally:
            session.close()

    def _config_schema(self) -> dict:
        return {
            "dataset_id": {"type": "string", "default": "", "label": "Dataset / Job ID"},
            "sample_count": {"type": "number", "default": 1, "label": "Number of images to load"},
        }


class ModelSelectorBlock(BlockBase):
    name = "model_select"
    display_name = "Select Model"
    description = "Run inference with a specific trained model (not just the active one)."
    category = "models"
    input_ports = [Port("image", "image", "Input image")]
    output_ports = [
        Port("detections", "detections", "Detection results"),
        Port("image", "image", "Original image (passthrough)"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        from lib.inference_engine import get_engine

        image = inputs["image"]
        model_id = self.config.get("model_id", "")
        conf = self.config.get("confidence", 0.25)

        engine = get_engine()
        if model_id and model_id != engine.model_id:
            engine.reload(model_id)

        detections = engine.predict_image(image, conf=conf)
        return BlockResult(
            outputs={"detections": detections, "image": image},
            metadata={"model": engine.model_name, "detections": len(detections)},
        )

    def _config_schema(self) -> dict:
        return {
            "model_id": {"type": "string", "default": "", "label": "Model ID (blank = active model)"},
            "confidence": {"type": "number", "default": 0.25, "min": 0, "max": 1, "label": "Confidence threshold"},
        }


class WebhookBlock(BlockBase):
    name = "webhook"
    display_name = "Send Webhook"
    description = "POST results to a webhook URL (Slack, email service, custom API)."
    category = "io"
    input_ports = [Port("data", "any", "Data to send")]
    output_ports = [Port("status", "number", "HTTP status code")]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        import json

        import httpx

        data = inputs.get("data")
        url = self.config.get("url", "")
        if not url:
            raise ValueError("No webhook URL configured")

        # Serialize data
        if hasattr(data, "__dict__"):
            payload = str(data)
        else:
            try:
                payload = json.dumps(data, default=str)
            except TypeError:
                payload = str(data)

        try:
            r = httpx.post(url, json={"data": payload}, timeout=10.0)
            status = r.status_code
        except Exception as e:
            status = 0
            payload = str(e)

        return BlockResult(
            outputs={"status": status},
            metadata={"url": url, "status": status},
        )

    def _config_schema(self) -> dict:
        return {"url": {"type": "string", "default": "", "label": "Webhook URL"}}


class TrainTriggerBlock(BlockBase):
    name = "train_trigger"
    display_name = "Start Training"
    description = "Trigger a model training run on a dataset."
    category = "platform"
    input_ports = [Port("dataset_id", "text", "Dataset/Job ID to train on", required=False)]
    output_ports = [
        Port("run_id", "text", "Training run ID"),
        Port("status", "text", "Initial status"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        from lib.db import LabelingJob, Project, SessionLocal, TrainingRun
        from lib.tasks import train_model

        dataset_id = inputs.get("dataset_id") or self.config.get("dataset_id", "")
        if not dataset_id:
            raise ValueError("No dataset_id provided")

        variant = self.config.get("model_variant", "yolo26n-seg")
        task_type = self.config.get("task_type", "segment")
        epochs = self.config.get("epochs", 50)

        session = SessionLocal()
        try:
            job = session.query(LabelingJob).filter_by(id=dataset_id).first()
            if not job:
                raise ValueError(f"Dataset {dataset_id} not found")

            project_id = job.project_id or session.query(Project).first().id
            run = TrainingRun(
                project_id=project_id,
                job_id=dataset_id,
                name=f"{job.text_prompt}_{variant}",
                task_type=task_type,
                model_variant=variant,
                hyperparameters={"epochs": epochs, "batch": 8, "imgsz": 640},
                dataset_minio_key=job.result_minio_key,
                total_epochs=epochs,
            )
            session.add(run)
            session.commit()

            task = train_model.delay(str(run.id))
            run.celery_task_id = task.id
            session.commit()

            return BlockResult(
                outputs={"run_id": str(run.id), "status": "queued"},
                metadata={"variant": variant, "epochs": epochs},
            )
        finally:
            session.close()

    def _config_schema(self) -> dict:
        return {
            "dataset_id": {"type": "string", "default": "", "label": "Dataset / Job ID"},
            "model_variant": {"type": "string", "default": "yolo26n-seg", "label": "Model variant"},
            "task_type": {"type": "string", "default": "segment", "label": "Task type"},
            "epochs": {"type": "number", "default": 50, "label": "Training epochs"},
        }
