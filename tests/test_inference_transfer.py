"""App containers and native workers exchange inference inputs through storage."""

import io
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.api import serve
from lib import storage, tasks
from tests.test_resource_authorization import resources as resource_rows

pytestmark = pytest.mark.no_auth_bypass

resources = resource_rows


@pytest.fixture
def transfer(resources, monkeypatch, tmp_path):
    _, client, member, rows = resources
    uploads, deleted, owners, temp_dirs = {}, [], [], []
    monkeypatch.setattr(storage, "upload_bytes", lambda key, data, **kw: uploads.setdefault(key, data) and key)
    monkeypatch.setattr(storage, "delete_object", lambda key: deleted.append(key), raising=False)
    monkeypatch.setattr(storage, "ensure_inference_lifecycle", lambda: None)
    monkeypatch.setattr(serve, "register_task_owner", lambda task_id, principal: owners.append(task_id))
    monkeypatch.setattr(serve, "_log_inference", lambda *args: None)

    def temp_dir(**kwargs):
        directory = tmp_path / str(uuid.uuid4())
        directory.mkdir()
        temp_dirs.append(directory)
        return str(directory)

    monkeypatch.setattr(serve.tempfile, "mkdtemp", temp_dir)
    return SimpleNamespace(
        client=client,
        workspace=member.workspace_id,
        rows=rows,
        uploads=uploads,
        deleted=deleted,
        owners=owners,
        temp_dirs=temp_dirs,
    )


@pytest.mark.parametrize("is_video", [False, True])
def test_sam_endpoints_dispatch_owned_stored_input_and_wait_off_event_loop(transfer, monkeypatch, is_video):
    payload = {"model_id": "sam3.1", "input_resolution": "80x48"}
    payload.update(
        {
            "frames": [
                {
                    "frame_index": 2,
                    "timestamp_s": 0.4,
                    "detections": [],
                    "source_width": 80,
                    "source_height": 48,
                    "frame_duration_s": 0.2,
                    "timestamp_method": "source_pts",
                }
            ],
            "total_frames": 1,
        }
        if is_video
        else {"detections": [], "count": 0}
    )
    calls = []

    def dispatch(*, args, task_id, **kwargs):
        assert task_id in transfer.owners
        assert args[0] in transfer.uploads
        assert args[0].startswith(f"inference/{transfer.workspace}/")
        assert args[1:] == [is_video, ["camera"], 0.2]
        caller = threading.get_ident()

        def get(*, timeout):
            assert timeout > 0
            assert threading.get_ident() != caller
            calls.append(task_id)
            return payload

        return SimpleNamespace(id=task_id, get=get)

    monkeypatch.setattr(serve, "predict_sam_task", SimpleNamespace(apply_async=dispatch), raising=False)
    content = b"video bytes"
    if not is_video:
        image = io.BytesIO()
        Image.new("RGB", (80, 48)).save(image, format="PNG")
        content = image.getvalue()
    response = transfer.client.post(
        "/api/v1/predict/sam" + ("/video" if is_video else ""),
        params={"prompts": "camera", "conf": 0.2},
        files={"file": ("input.mp4" if is_video else "input.png", content)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["model_id"] == "sam3.1"
    assert calls
    if is_video:
        assert response.json()["frames"][0]["timestamp_method"] == "source_pts"
        assert response.json()["frames"][0]["source_width"] == 80


def test_long_video_dispatches_object_key_and_removes_app_temp_file(transfer, monkeypatch):
    import cv2

    from lib import video_tracker

    monkeypatch.setattr(video_tracker, "validate_video", lambda path: None)
    monkeypatch.setattr(
        cv2, "VideoCapture", lambda path: SimpleNamespace(get=lambda property: 600, release=lambda: None)
    )
    dispatched = []

    def dispatch(*, args, task_id, **kwargs):
        assert task_id in transfer.owners and args[2] in transfer.owners
        dispatched.append(args)
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(serve.predict_video_task, "apply_async", dispatch)
    response = transfer.client.post(
        "/api/v1/predict/video",
        params={"model_id": str(transfer.rows["own"]["model"].id)},
        files={"file": ("drive.mp4", b"original video")},
    )
    assert response.status_code == 202, response.text
    input_key = dispatched[0][0]
    assert input_key.startswith(f"inference/{transfer.workspace}/")
    assert transfer.uploads[input_key] == b"original video"
    assert all(not directory.exists() for directory in transfer.temp_dirs)


def test_comparison_dispatches_stored_input_and_cleans_upload_when_enqueue_fails(transfer, monkeypatch):
    dispatched = []

    def dispatch(*, args, task_id, **kwargs):
        dispatched.append(args)
        assert args[1] in transfer.uploads
        assert args[0] in transfer.owners and task_id in transfer.owners
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(tasks.compare_models_task, "apply_async", dispatch)
    with pytest.raises(RuntimeError, match="broker unavailable"):
        transfer.client.post(
            "/api/v1/comparisons/run",
            params={"model_a_id": "sam3.1", "model_b_id": "sam3.1"},
            files={"file": ("sample.png", b"original image")},
        )
    assert len(dispatched) == 1
    assert transfer.deleted == [dispatched[0][1]]
    assert all(not directory.exists() for directory in transfer.temp_dirs)


@pytest.mark.parametrize("kind", ["prediction", "comparison", "sam"])
@pytest.mark.parametrize("fails", [False, True])
def test_worker_downloads_input_on_its_host_and_cleans_up_even_on_failure(monkeypatch, kind, fails):
    from lib import redis_client

    monkeypatch.setattr(
        redis_client,
        "get_redis",
        lambda: SimpleNamespace(get=lambda key: None, setex=lambda *a: None, publish=lambda *a: None),
    )
    input_key = f"inference/{uuid.uuid4()}/{uuid.uuid4()}/input.mp4"
    downloaded, deleted = [], []

    def download(key, path):
        assert key == input_key
        downloaded.append(Path(path))
        Path(path).write_bytes(b"source bytes")
        return Path(path)

    def infer(*args, **kwargs):
        assert downloaded and downloaded[0].exists()
        assert downloaded[0].read_bytes() == b"source bytes"
        assert str(downloaded[0]) in args
        if fails:
            raise RuntimeError("inference failed")
        return {"status": "completed"}

    monkeypatch.setattr(storage, "download_file", download)
    monkeypatch.setattr(storage, "delete_object", deleted.append, raising=False)
    monkeypatch.setattr(tasks, "_native_video_available", lambda: True)
    monkeypatch.setattr(tasks, "_predict_video_from_file", infer, raising=False)
    monkeypatch.setattr(tasks, "_compare_models_from_file", infer, raising=False)
    monkeypatch.setattr(tasks, "_sam_video_result", infer, raising=False)
    assert hasattr(tasks, "predict_sam_task"), "SAM must execute as a worker task"
    run = {
        "prediction": lambda: tasks.predict_video_task.run(input_key, 0.2, "session", model_id="model-a"),
        "comparison": lambda: tasks.compare_models_task.run("session", input_key, True, "a", "b", 0.2),
        "sam": lambda: tasks.predict_sam_task.run(input_key, True, ["camera"], 0.2),
    }[kind]
    if fails and kind == "sam":
        with pytest.raises(RuntimeError, match="inference failed"):
            run()
    elif fails:
        assert run()["status"] == "failed"
    else:
        assert run()["status"] == "completed"
    assert downloaded and not downloaded[0].parent.exists()
    assert deleted == [input_key]


def test_sam_video_worker_retains_empty_assessments_timing_and_pixel_masks(monkeypatch):
    raw = [
        {
            "frame_idx": 2,
            "timestamp_s": 0.4,
            "width": 80,
            "height": 48,
            "frame_duration_s": 0.2,
            "timestamp_method": "source_pts",
            "detections": [],
        },
        {
            "frame_idx": 3,
            "timestamp_s": 0.6,
            "width": 80,
            "height": 48,
            "frame_duration_s": 0.1,
            "timestamp_method": "source_pts",
            "detections": [
                {
                    "label": "camera",
                    "score": 0.9,
                    "bbox": [8, 12, 40, 36],
                    "track_id": 7,
                    "polygon": [0.1, 0.25, 0.5, 0.25, 0.5, 0.75],
                }
            ],
        },
    ]
    monkeypatch.setitem(
        sys.modules, "labeler.video_labeler", SimpleNamespace(process_video_native=lambda *a, **kw: raw)
    )
    result = tasks._sam_video_result("local.mp4", ["camera"], 0.2)
    assert result["total_frames"] == 2
    assert result["frames"][0]["detections"] == []
    assert result["frames"][1]["timestamp_s"] == 0.6
    assert result["frames"][1]["frame_duration_s"] == 0.1
    assert result["frames"][1]["timestamp_method"] == "source_pts"
    assert result["frames"][1]["detections"][0]["mask"] == [[8, 12], [40, 12], [40, 36]]
    assert result["frames"][1]["detections"][0]["track_id"] == 7


@pytest.mark.parametrize("key", ["/tmp/input.mp4", "videos/persistent.mp4", "inference/bad/bad/input.mp4"])
def test_worker_refuses_paths_and_persistent_objects_without_deleting_them(monkeypatch, key):
    deleted = []
    monkeypatch.setattr(storage, "delete_object", deleted.append)
    with pytest.raises(ValueError), tasks._inference_input(key):
        pytest.fail("invalid inference reference reached worker")
    assert deleted == []


def test_sam_worker_timeout_returns_503_without_deleting_an_active_worker_input(transfer, monkeypatch):
    from celery.exceptions import TimeoutError

    def timeout(**kwargs):
        raise TimeoutError("worker timed out")

    monkeypatch.setattr(
        serve, "predict_sam_task", SimpleNamespace(apply_async=lambda **kw: SimpleNamespace(get=timeout))
    )
    response = transfer.client.post(
        "/api/v1/predict/sam/video", params={"prompts": "camera"}, files={"file": ("input.mp4", b"video")}
    )
    assert response.status_code == 503
    assert transfer.uploads and not transfer.deleted
