"""Detection export publication and training handoff for transformer jobs."""

import io
import json
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from PIL import Image

from app.api import review, train
from lib.db import TrainingRun
from tests.test_resource_authorization import resources as resource_rows

resources = resource_rows


@pytest.fixture
def export_artifacts(resources, monkeypatch):
    session, _, _, rows = resources
    own = rows["own"]
    own["job"].task_type = "detect_transformer"
    own["annotation"].bbox = [0.5, 0.5, 0.2, 0.2]
    own["annotation"].polygon = [0.4, 0.4, 0.6, 0.4, 0.6, 0.6]
    session.commit()
    image = io.BytesIO()
    Image.new("RGB", (32, 32)).save(image, format="JPEG")
    artifacts = {}
    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(image.getvalue()))
    monkeypatch.setattr(review, "upload_file", lambda key, path: artifacts.update({key: path.read_bytes()}))
    return artifacts


def test_transformer_detect_export_publishes_actual_zip_and_training_snapshot(resources, export_artifacts, monkeypatch):
    session, client, _, rows = resources
    job = rows["own"]["job"]
    request = {"job_id": str(job.id), "task_type": "detect_transformer", "model_variant": "rf-detr-base"}
    monkeypatch.setattr(train.train_model, "delay", lambda run_id: SimpleNamespace(id="queued-training"))
    assert client.post("/api/v1/train", json=request).status_code == 400

    response = client.post(f"/api/v1/jobs/{job.id}/export", json={"format": "detect"})

    assert response.status_code == 200, response.text
    session.refresh(job)
    assert job.result_minio_key in export_artifacts
    assert response.json()["download_url"] == f"/media/{job.result_minio_key}"
    assert job.task_type == "detect_transformer"
    with zipfile.ZipFile(io.BytesIO(export_artifacts[job.result_minio_key])) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["task"] == "detect"
        label = archive.read(manifest["samples"][0]["label_path"]).decode()
        assert label.strip() == "0 0.500000 0.500000 0.200000 0.200000"

    response = client.post("/api/v1/train", json=request)

    assert response.status_code == 202, response.text
    run = session.get(TrainingRun, uuid.UUID(response.json()["run_id"]))
    assert run.dataset_minio_key == job.result_minio_key
    assert run.task_type == "detect_transformer"


@pytest.mark.parametrize(
    "task_type, fmt",
    [
        ("detect_transformer", "segment"),
        ("detect_transformer", "obb"),
        ("detect_transformer", "classify"),
        ("detect_transformer", "pose"),
        ("segment", "detect"),
        ("detect", "segment"),
    ],
)
def test_other_task_formats_do_not_replace_current_training_artifact(resources, export_artifacts, task_type, fmt):
    session, client, _, rows = resources
    job = rows["own"]["job"]
    job.task_type = task_type
    job.result_minio_key = "results/current-snapshot.zip"
    session.commit()

    response = client.post(f"/api/v1/jobs/{job.id}/export", json={"format": fmt})

    assert response.status_code == 200, response.text
    assert len(export_artifacts) == 1
    session.refresh(job)
    assert job.result_minio_key == "results/current-snapshot.zip"


def test_unset_task_retains_segment_export_default(resources, export_artifacts):
    session, client, _, rows = resources
    job = rows["own"]["job"]
    job.task_type = None
    session.commit()

    response = client.post(f"/api/v1/jobs/{job.id}/export", json={"format": "segment"})

    assert response.status_code == 200, response.text
    session.refresh(job)
    assert job.result_minio_key in export_artifacts


def test_transformer_export_revision_check_rejects_annotation_edit_during_upload(
    resources, export_artifacts, monkeypatch
):
    session, client, _, rows = resources
    own = rows["own"]
    original_revision = own["job"].evidence_revision
    deleted = []

    def edit_during_upload(key, path):
        export_artifacts[key] = path.read_bytes()
        response = client.patch(f"/api/v1/annotations/{own['annotation'].id}", json={"bbox": [0.5, 0.5, 0.3, 0.3]})
        assert response.status_code == 200, response.text

    monkeypatch.setattr(review, "upload_file", edit_during_upload)
    monkeypatch.setattr(review, "delete_object", deleted.append)

    response = client.post(f"/api/v1/jobs/{own['job'].id}/export", json={"format": "detect"})

    assert response.status_code == 409, response.text
    assert deleted == list(export_artifacts)
    session.refresh(own["job"])
    assert own["job"].result_minio_key is None
    assert own["job"].evidence_revision == original_revision + 1
