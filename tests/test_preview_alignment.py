"""The Playground worker and direct response retain decoded-frame timing."""

import base64
import shutil
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from tests.test_video_alignment import _box
from tests.test_video_alignment import moving_video as moving_video
from tests.test_video_evidence_regressions import _load_subject

pytestmark = pytest.mark.no_auth_bypass


def _raw_preview():
    return {
        "frames": [
            {
                "frame_index": 2,
                "timestamp_s": 0.48,
                "frame_duration_s": 0.12,
                "timestamp_method": "source_pts",
                "width": 80,
                "height": 48,
                "source_width": 80,
                "source_height": 48,
                "image_b64": "jpeg",
                "detections": [],
            }
        ],
        "total_detections": 0,
        "fps": 4.6875,
        "video_duration_s": 2.56,
        "mode": "window",
    }


def test_direct_preview_normalization_preserves_source_timing_and_dimensions():
    from app.api.label import _preview_result_to_response

    response = _preview_result_to_response(_raw_preview()).model_dump()
    frame = response["frames"][0]
    assert frame["frame_idx"] == 2
    assert frame["frame_duration_s"] == 0.12
    assert frame["timestamp_method"] == "source_pts"
    assert (frame["source_width"], frame["source_height"]) == (80, 48)


def test_queued_preview_returns_the_same_public_frame_contract(monkeypatch):
    import lib.tasks

    monkeypatch.setitem(
        sys.modules, "labeler.video_labeler", SimpleNamespace(run_playground=lambda *a, **kw: _raw_preview())
    )
    response = lib.tasks.label_playground.run("video", ["moving"])
    frame = response["frames"][0]
    assert frame["frame_idx"] == 2
    assert frame["timestamp_method"] == "source_pts"
    from app.api.label import _preview_result_to_response

    assert response == _preview_result_to_response(response).model_dump()


def test_native_playground_window_is_selected_by_source_pts_and_thumbnail_matches(monkeypatch, moving_video):
    video, timestamps = moving_video(vfr=True)
    subject = _load_subject("labeler.video_labeler")
    core = SimpleNamespace(array=np.array)
    for name, module in {
        "mlx": SimpleNamespace(core=core),
        "mlx.core": core,
        "mlx_vlm.generate": SimpleNamespace(wired_limit=lambda model: nullcontext()),
        "mlx_vlm.models.sam3.generate": SimpleNamespace(
            SimpleTracker=lambda: SimpleNamespace(update=lambda result: result)
        ),
        "mlx_vlm.models.sam3_1.generate": SimpleNamespace(_get_backbone_features=lambda model, pixels: pixels),
        "labeler.sam3_optimized": SimpleNamespace(
            detect_with_backbone_fast=lambda predictor, pixels, prompts, *a, **kw: SimpleNamespace(
                scores=[0.9],
                boxes=np.array([_box(pixels)]),
                masks=np.array([pixels[:, :, 0] > 200]),
                labels=prompts,
                track_ids=None,
            )
        ),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        subject,
        "_get_predictor",
        lambda *a: SimpleNamespace(
            model=object(),
            processor=SimpleNamespace(preprocess_image=lambda image: {"pixel_values": np.asarray(image)}),
        ),
    )
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one.return_value = SimpleNamespace(minio_key="source.mp4")
    monkeypatch.setattr(subject, "SessionLocal", lambda: session)
    monkeypatch.setattr(subject, "download_file", lambda key, destination: shutil.copyfile(video, destination))
    result = subject.run_playground("video", ["moving"], start_sec=0.1, duration_sec=0.05, sample_fps=100)
    assert len(result["frames"]) == 1
    frame = result["frames"][0]
    assert frame["frame_index"] == 1
    assert frame["timestamp_s"] == timestamps[1]
    assert frame["frame_duration_s"] == pytest.approx(timestamps[2] - timestamps[1])
    assert frame["timestamp_method"] == "source_pts"
    image = cv2.imdecode(np.frombuffer(base64.b64decode(frame["image_b64"]), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert _box(image) == pytest.approx(frame["detections"][0]["bbox"], abs=1)


def test_legacy_preview_timing_is_unknown_instead_of_claiming_source_pts():
    from app.api.label import _preview_result_to_response

    raw = _raw_preview()
    del raw["frames"][0]["timestamp_method"]
    del raw["frames"][0]["frame_duration_s"]
    response = _preview_result_to_response(raw).model_dump()
    assert response["frames"][0]["timestamp_method"] == "unknown"
    assert response["frames"][0]["frame_duration_s"] is None
