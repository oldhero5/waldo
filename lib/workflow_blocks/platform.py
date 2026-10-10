"""Workspace-authorized dataset, model, training, and webhook workflow blocks."""

import ipaddress
import json
import os
import socket
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from lib.authorization import (
    WorkspacePrincipal,
    require_resource,
    require_scope,
    require_workspace_role,
    resolve_workspace,
    scope_resources,
)
from lib.workflow_blocks.base import BlockBase, BlockResult, Port


def _execution_principal(block: BlockBase, session, *, write: bool = False) -> WorkspacePrincipal:
    """Authority comes only from the executor, then current membership is checked."""
    principal = getattr(block, "execution_principal", None)
    if not isinstance(principal, WorkspacePrincipal):
        raise HTTPException(status_code=401, detail="Workflow execution principal is required")
    current = resolve_workspace(session, principal.identity, principal.workspace_id)
    if write:
        require_workspace_role(current, "admin", "editor")
    else:
        require_scope(current.identity, "read")
    return current


def get_workspace_model_engine(block: BlockBase):
    """Select an owned model ID first, then obtain its stable pool entry."""
    from lib.db import ModelRegistry, SessionLocal
    from lib.inference_engine import get_pool

    with SessionLocal() as session:
        principal = _execution_principal(block, session)
        model_id = block.config.get("model_id")
        if model_id:
            model = require_resource(session, principal, ModelRegistry, model_id)
        else:
            model = (
                scope_resources(session.query(ModelRegistry), ModelRegistry, principal)
                .filter_by(is_active=True)
                .first()
            )
            if model is None:
                raise ValueError("No active model in this workspace; configure an owned model ID")
        selected_id = str(model.id)
    return get_pool().get_model(selected_id)


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

        from lib.db import Annotation, Frame, LabelingJob, SessionLocal
        from lib.storage import download_file

        dataset_id = self.config.get("dataset_id", "")
        sample_count = int(self.config.get("sample_count", 1))
        if not dataset_id:
            raise ValueError("No dataset_id configured. Select a dataset in block settings.")
        if not 1 <= sample_count <= 100:
            raise ValueError("sample_count must be between 1 and 100")
        with SessionLocal() as session:
            principal = _execution_principal(self, session)
            job = require_resource(session, principal, LabelingJob, dataset_id)
            frames = (
                scope_resources(session.query(Frame), Frame, principal)
                .join(Annotation, Annotation.frame_id == Frame.id)
                .filter(Annotation.job_id == job.id)
                .distinct()
                .order_by(Frame.timestamp_s, Frame.id)
                .limit(sample_count)
                .all()
            )
            if not frames:
                raise ValueError("No labeled frames found in dataset")
            with tempfile.TemporaryDirectory(prefix="waldo_workflow_") as temporary:
                image_path = Path(temporary) / "frame.jpg"
                download_file(frames[0].minio_key, image_path)
                image = cv2.imread(str(image_path))
                if image is None:
                    raise ValueError("Dataset frame is not a valid image")
            return BlockResult(
                outputs={"image": image, "count": len(frames)},
                metadata={"dataset": job.text_prompt or dataset_id, "frames": len(frames)},
            )

    def _config_schema(self) -> dict:
        return {
            "dataset_id": {"type": "string", "default": "", "label": "Dataset / Job ID"},
            "sample_count": {"type": "number", "default": 1, "min": 1, "max": 100, "label": "Number of images to load"},
        }


class ModelSelectorBlock(BlockBase):
    name = "model_select"
    display_name = "Select Model"
    description = "Run inference with a specific trained model in the current workspace."
    category = "models"
    input_ports = [Port("image", "image", "Input image")]
    output_ports = [
        Port("detections", "detections", "Detection results"),
        Port("image", "image", "Original image (passthrough)"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        image = inputs["image"]
        engine = get_workspace_model_engine(self)
        detections = engine.predict_image(image, conf=self.config.get("confidence", 0.25))
        return BlockResult(
            outputs={"detections": detections, "image": image},
            metadata={"model": engine.model_name, "detections": len(detections)},
        )

    def _config_schema(self) -> dict:
        return {
            "model_id": {"type": "string", "default": "", "label": "Model ID (blank = workspace active model)"},
            "confidence": {"type": "number", "default": 0.25, "min": 0, "max": 1, "label": "Confidence threshold"},
        }


def _webhook_destination(url: str) -> tuple[str, str]:
    """Resolve once and pin the connection to a validated IP (no DNS rebinding)."""
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
    ):
        raise ValueError("Webhook URL must be HTTP(S), without credentials or fragments")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Webhook hostname has no addresses")
    ips = [ipaddress.ip_address(address[4][0]) for address in addresses]
    # This is operator environment configuration; graph settings cannot opt in.
    allow_private = os.environ.get("WALDO_WORKFLOW_ALLOW_PRIVATE_WEBHOOKS", "").lower() in {"1", "true", "yes"}
    if not allow_private and any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in ips):
        raise ValueError("Webhook destination must resolve only to public IP addresses")
    return parts.hostname, str(ips[0])


class WebhookBlock(BlockBase):
    name = "webhook"
    display_name = "Send Webhook"
    description = "POST results to a public webhook URL. Private destinations require operator configuration."
    category = "io"
    input_ports = [Port("data", "any", "Data to send")]
    output_ports = [Port("status", "number", "HTTP status code")]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        import httpx

        from lib.db import SessionLocal

        with SessionLocal() as session:
            _execution_principal(self, session, write=True)
        url = self.config.get("url", "")
        if not url:
            raise ValueError("No webhook URL configured")
        hostname, address = _webhook_destination(url)
        payload = json.dumps(inputs.get("data"), default=str)
        # Keep the original Host header and TLS name, but connect to the pinned IP.
        with httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False) as client:
            request = client.build_request("POST", url, json={"data": payload})
            request.url = request.url.copy_with(host=address)
            request.extensions["sni_hostname"] = hostname
            response = client.send(request)
            response.raise_for_status()
            status = response.status_code
        return BlockResult(outputs={"status": status}, metadata={"url": url, "status": status})

    def _config_schema(self) -> dict:
        return {"url": {"type": "string", "default": "", "label": "Webhook URL"}}


class TrainTriggerBlock(BlockBase):
    name = "train_trigger"
    display_name = "Start Training"
    description = "Trigger a model training run on a dataset in the current workspace."
    category = "platform"
    input_ports = [Port("dataset_id", "text", "Dataset/Job ID to train on", required=False)]
    output_ports = [Port("run_id", "text", "Training run ID"), Port("status", "text", "Initial status")]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        from lib.db import LabelingJob, Project, SessionLocal, TrainingRun, Video
        from lib.tasks import train_model

        dataset_id = inputs.get("dataset_id") or self.config.get("dataset_id", "")
        if not dataset_id:
            raise ValueError("No dataset_id provided")
        variant = self.config.get("model_variant", "yolo26n-seg")
        task_type = self.config.get("task_type", "segment")
        epochs = int(self.config.get("epochs", 50))
        if not 1 <= epochs <= 1000:
            raise ValueError("epochs must be between 1 and 1000")
        with SessionLocal() as session:
            principal = _execution_principal(self, session, write=True)
            job = require_resource(session, principal, LabelingJob, dataset_id)
            if job.status != "completed" or not job.result_minio_key:
                raise ValueError("Training requires a completed dataset with exported labels")
            project_id = job.project_id
            if project_id is None and job.video_id is not None:
                project_id = require_resource(session, principal, Video, job.video_id).project_id
            if project_id is None:
                raise ValueError("Dataset has no owning project")
            require_resource(session, principal, Project, project_id)
            run = TrainingRun(
                project_id=project_id,
                job_id=job.id,
                name=f"{job.text_prompt or 'dataset'}_{variant}",
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
                outputs={"run_id": str(run.id), "status": "queued"}, metadata={"variant": variant, "epochs": epochs}
            )

    def _config_schema(self) -> dict:
        return {
            "dataset_id": {"type": "string", "default": "", "label": "Dataset / Job ID"},
            "model_variant": {"type": "string", "default": "yolo26n-seg", "label": "Model variant"},
            "task_type": {"type": "string", "default": "segment", "label": "Task type"},
            "epochs": {"type": "number", "default": 50, "min": 1, "max": 1000, "label": "Training epochs"},
        }
