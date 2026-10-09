"""Reviewed exports use current object regions, never inferred anatomical points."""

import io
import json
import uuid
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from fastapi import HTTPException
from PIL import Image

from app.api import review, train
from lib.db import TrainingRun
from tests.test_resource_authorization import resources as resource_rows

resources = resource_rows
pytestmark = pytest.mark.no_auth_bypass


def observation(name="camera", **changes):
    return SimpleNamespace(
        **{
            "id": str(uuid.uuid4()),
            "class_name": name,
            "class_index": 7,
            "status": "accepted",
            "polygon": [0.2, 0.2, 0.6, 0.2, 0.6, 0.6, 0.2, 0.6],
            "bbox": [0.4, 0.4, 0.4, 0.4],
        }
        | changes
    )


@pytest.fixture
def pixels(monkeypatch):
    calls = []
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[:, :, 0] = np.arange(100)[None, :]
    image[:, :, 1] = np.arange(100)[:, None]

    def download(key, path):
        calls.append(key)
        Image.fromarray(image).save(path)

    monkeypatch.setattr(review, "download_file", download)
    return calls


def test_classification_export_crops_reviewed_regions_and_groups_source_videos(tmp_path, pixels):
    first, second, rejected = observation(), observation(status="pending"), observation(status="rejected")
    third = observation("truck", polygon=[0.5, 0.5, 0.9, 0.5, 0.9, 0.9, 0.5, 0.9])
    frames = {
        "a": SimpleNamespace(video_id="video-a", minio_key="a.png"),
        "b": SimpleNamespace(video_id="video-b", minio_key="b.png"),
    }
    root = tmp_path / "dataset"
    review._write_review_export(
        root,
        tmp_path / "sources",
        frames,
        {"a": [third, first, rejected], "b": [second]},
        ["camera", "truck"],
        "classify",
    )
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["version"] == 2
    assert manifest["geometry_method"] == "polygon_envelope_padding_5px_v1"
    assert manifest["split_strategy"] == "group"
    assert len(manifest["samples"]) == 3
    assert {sample["source_annotation_id"] for sample in manifest["samples"]} == {first.id, second.id, third.id}
    assert {sample["split"] for sample in manifest["samples"]} == {"train", "val"}
    for sample in manifest["samples"]:
        assert sample["label_path"] is None
        crop = np.array(Image.open(root / sample["image_path"]))
        assert crop.shape == (50, 50, 3)
        expected = [65, 65] if sample["source_annotation_id"] == third.id else [35, 35]
        assert crop[20, 20, :2].astype(int).tolist() == pytest.approx(expected, abs=3)
    same_frame = [sample for sample in manifest["samples"] if sample["source_frame_id"] == "a"]
    assert [sample["source_annotation_id"] for sample in same_frame] == sorted([first.id, third.id])
    assert len({sample["split"] for sample in same_frame}) == 1
    assert first.class_index == second.class_index == 7


def test_pose_export_uses_polygon_centroid_and_current_edited_box(tmp_path, pixels):
    ann = observation(polygon=[0.1, 0.1, 0.9, 0.1, 0.1, 0.9], bbox=[0.4, 0.4, 0.6, 0.6])
    root = tmp_path / "dataset"
    review._write_review_export(
        root,
        tmp_path / "sources",
        {"a": SimpleNamespace(video_id="source", minio_key="a.png")},
        {"a": [ann]},
        ["camera"],
        "pose",
    )
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["version"] == 2
    assert manifest["geometry_method"] == "polygon_centroid_single_keypoint_v1"
    sample = manifest["samples"][0]
    assert sample["source_annotation_ids"] == [ann.id]
    values = (root / sample["label_path"]).read_text().split()
    assert [float(v) for v in values] == pytest.approx([0, 0.4, 0.4, 0.6, 0.6, 11 / 30, 11 / 30, 2], abs=1e-6)
    assert yaml.safe_load((root / "data.yaml").read_text())["kpt_shape"] == [1, 3]
    assert ann.class_index == 7


def test_pose_export_uses_polygon_bounds_only_when_box_absent(tmp_path, pixels):
    root = tmp_path / "dataset"
    ann = observation(bbox=None)
    review._write_review_export(
        root,
        tmp_path / "sources",
        {"a": SimpleNamespace(video_id="source", minio_key="a.png")},
        {"a": [ann]},
        ["camera"],
        "pose",
    )
    label = next((root / "labels/train").glob("*.txt")).read_text().split()
    assert [float(v) for v in label] == pytest.approx([0, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 2], abs=1e-6)


def test_pose_centroid_remains_defined_for_tiny_normalized_region(tmp_path, pixels):
    root = tmp_path / "dataset"
    ann = observation(polygon=[0.5, 0.5, 0.5001, 0.5, 0.5001, 0.5001, 0.5, 0.5001], bbox=None)
    review._write_review_export(
        root,
        tmp_path / "sources",
        {"a": SimpleNamespace(video_id="source", minio_key="a.png")},
        {"a": [ann]},
        ["camera"],
        "pose",
    )
    label = next((root / "labels/train").glob("*.txt")).read_text().split()
    assert [float(v) for v in label] == pytest.approx(
        [0, 0.50005, 0.50005, 0.0001, 0.0001, 0.50005, 0.50005, 2], abs=1e-6
    )


@pytest.mark.parametrize(
    "box", [[0.8, 0.8, 0.2, 0.2], [0.5, 0.5, -0.2, 0.2], [0.1, 0.1, 0.4, 0.4], [0.5, 0.5, float("nan"), 0.2], []]
)
def test_pose_rejects_invalid_or_contradictory_edited_box(tmp_path, pixels, box):
    with pytest.raises(HTTPException) as error:
        review._write_review_export(
            tmp_path / "dataset",
            tmp_path / "sources",
            {"a": SimpleNamespace(video_id="source", minio_key="a.png")},
            {"a": [observation(bbox=box)]},
            ["camera"],
            "pose",
        )
    assert error.value.status_code == 400
    assert not (tmp_path / "dataset/manifest.json").exists()
    assert not pixels


@pytest.mark.parametrize("fmt", ["classify", "pose"])
@pytest.mark.parametrize(
    "polygon", [[], [0, 0, 1, 0, 1], [0, 0, 1.1, 0, 1, 1], [0, 0, 0.5, 0.5, 1, 1], [0, 0, float("nan"), 0, 1, 1]]
)
def test_new_export_formats_fail_closed_on_invalid_polygon(tmp_path, pixels, fmt, polygon):
    with pytest.raises(HTTPException) as error:
        review._write_review_export(
            tmp_path / "dataset",
            tmp_path / "sources",
            {"a": SimpleNamespace(video_id="source", minio_key="a.png")},
            {"a": [observation(polygon=polygon)]},
            ["camera"],
            fmt,
        )
    assert error.value.status_code == 400
    assert not (tmp_path / "dataset/manifest.json").exists()


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", ".", "..", "bad\x00name", ""])
def test_classification_rejects_unsafe_directory_name_before_source_io(tmp_path, pixels, name):
    with pytest.raises(HTTPException) as error:
        review._write_review_export(
            tmp_path / "dataset",
            tmp_path / "sources",
            {"a": SimpleNamespace(video_id="source", minio_key="a.png")},
            {"a": [observation(name)]},
            [name],
            "classify",
        )
    assert error.value.status_code == 400
    assert "class name" in error.value.detail.lower()
    assert not pixels


@pytest.mark.parametrize("fmt", ["classify", "pose"])
def test_owned_new_exports_are_immutable_and_review_edits_invalidate_training_artifact(
    resources, pixels, monkeypatch, fmt
):
    session, client, member, rows = resources
    own = rows["own"]
    own["job"].task_type = fmt
    own["annotation"].polygon = [0.1, 0.1, 0.7, 0.1, 0.7, 0.7, 0.1, 0.7]
    own["annotation"].bbox = [0.4, 0.4, 0.6, 0.6]
    own["run"].dataset_minio_key = "prior-run.zip"
    session.commit()
    uploads = {}
    monkeypatch.setattr(review, "upload_file", lambda key, path: uploads.setdefault(key, path.read_bytes()))
    url = f"/api/v1/jobs/{own['job'].id}/export"
    assert client.post(f"/api/v1/jobs/{rows['foreign']['job'].id}/export", json={"format": fmt}).status_code == 404
    member.role = "viewer"
    session.commit()
    assert client.post(url, json={"format": fmt}).status_code == 403
    member.role = "admin"
    session.commit()
    assert client.post(url, json={"format": fmt}).status_code == 200
    session.refresh(own["job"])
    first_key = own["job"].result_minio_key
    with zipfile.ZipFile(io.BytesIO(uploads[first_key])) as archive:
        assert json.loads(archive.read("manifest.json"))["task"] == fmt
    assert client.post(url, json={"format": "detect"}).status_code == 200
    session.refresh(own["job"])
    assert own["job"].result_minio_key == first_key
    monkeypatch.setattr(train.train_model, "delay", lambda *a: SimpleNamespace(id="training-task"))
    started = client.post("/api/v1/train", json={"job_id": str(own["job"].id), "task_type": fmt})
    assert started.status_code == 202, started.text
    new_run = session.get(TrainingRun, uuid.UUID(started.json()["run_id"]))
    assert new_run.dataset_minio_key == first_key
    assert (
        client.patch(
            f"/api/v1/annotations/{own['annotation'].id}", json={"polygon": [0.2, 0.2, 0.6, 0.2, 0.6, 0.6, 0.2, 0.6]}
        ).status_code
        == 200
    )
    session.refresh(own["job"])
    assert own["job"].result_minio_key is None
    assert client.post(url, json={"format": fmt}).status_code == 200
    session.refresh(own["job"])
    assert own["job"].result_minio_key != first_key
    assert first_key in uploads
    assert own["run"].dataset_minio_key == "prior-run.zip"
    assert new_run.dataset_minio_key == first_key
