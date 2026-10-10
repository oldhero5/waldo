"""Native EOF must agree with decoded source evidence, not rate estimates."""

import platform
import shutil
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from PIL import Image

from tests.test_video_evidence_regressions import _load_subject

pytestmark = pytest.mark.no_auth_bypass


@pytest.fixture
def native_subject(monkeypatch):
    # Replace model inference only; timing, decoding, and sampling remain real.
    monkeypatch.setitem(sys.modules, "labeler.pipeline", SimpleNamespace(_update_job=lambda *a, **kw: None))
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
            detect_with_backbone_fast=lambda *a, **kw: SimpleNamespace(
                scores=[], boxes=[], masks=[], labels=[], track_ids=None
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
    return subject


@pytest.fixture
def estimated_count_video(tmp_path):
    # Matroska has no nb_frames here, so duration * nominal FPS overcounts VFR.
    for index in range(6):
        Image.new("RGB", (40, 40), (index * 30, 0, 0)).save(tmp_path / f"{index}.png")
    source = tmp_path / "frames.txt"
    source.write_text(
        "".join(f"file '{index}.png'\nduration {0.12 if index % 2 == 0 else 0.36}\n" for index in range(6))
    )
    video = tmp_path / "vfr.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(source),
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    return video


def test_native_accepts_normal_eof_with_inflated_frame_count(native_subject, estimated_count_video):
    capture = cv2.VideoCapture(str(estimated_count_video))
    try:
        assert capture.get(cv2.CAP_PROP_FRAME_COUNT) > 6
    finally:
        capture.release()

    results = native_subject.process_video_native(str(estimated_count_video), ["camera"], sample_fps=100)

    assert [frame["frame_idx"] for frame in results] == [0, 1, 2, 3, 4, 5]
    assert [frame["timestamp_s"] for frame in results] == pytest.approx([0, 0.12, 0.48, 0.60, 0.96, 1.08])
    assert all(frame["timestamp_method"] == "source_pts" and frame["detections"] == [] for frame in results)


@pytest.mark.parametrize("reported_count", [3, 28])
def test_native_rejects_decode_gap_against_source_frames(
    monkeypatch, native_subject, estimated_count_video, reported_count
):
    capture = cv2.VideoCapture(str(estimated_count_video))
    reads = 0
    released = False

    def read():
        nonlocal reads
        if reads == 3:
            return False, None
        reads += 1
        return capture.read()

    def release():
        nonlocal released
        released = True
        capture.release()

    partial_capture = SimpleNamespace(
        isOpened=capture.isOpened,
        get=lambda prop: reported_count if prop == cv2.CAP_PROP_FRAME_COUNT else capture.get(prop),
        read=read,
        release=release,
    )
    monkeypatch.setattr(native_subject.cv2, "VideoCapture", lambda *a: partial_capture)

    with pytest.raises(RuntimeError, match="source frame 3"):
        native_subject.process_video_native(str(estimated_count_video), ["camera"], sample_fps=100)
    assert released


def test_native_rejects_more_decoded_frames_than_source_evidence(monkeypatch, native_subject, estimated_count_video):
    timings = native_subject.probe_frame_timing(str(estimated_count_video))
    monkeypatch.setattr(native_subject, "probe_frame_timing", lambda path: timings[:5])
    capture = cv2.VideoCapture(str(estimated_count_video))
    monkeypatch.setattr(
        native_subject.cv2,
        "VideoCapture",
        lambda *a: SimpleNamespace(
            isOpened=capture.isOpened,
            get=lambda prop: 5 if prop == cv2.CAP_PROP_FRAME_COUNT else capture.get(prop),
            read=capture.read,
            release=capture.release,
        ),
    )

    with pytest.raises(RuntimeError):
        native_subject.process_video_native(str(estimated_count_video), ["camera"], sample_fps=100)


@pytest.fixture
def damaged_video(tmp_path):
    video = tmp_path / "damaged.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=8:duration=2",
            "-c:v",
            "mpeg2video",
            "-f",
            "mpegts",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    video.write_bytes(video.read_bytes()[:-500])
    return video


def test_native_rejects_ffprobe_decode_errors_even_when_it_returns_frames(native_subject, damaged_video):
    with pytest.raises(RuntimeError, match="decod"):
        native_subject.process_video_native(str(damaged_video), ["camera"], sample_fps=100)


@pytest.mark.parametrize("native", [False, True])
def test_preview_probe_failure_does_not_leave_capture_open(monkeypatch, native_subject, damaged_video, native):
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one.return_value = SimpleNamespace(minio_key="source")
    monkeypatch.setattr(native_subject, "SessionLocal", lambda: session)
    monkeypatch.setattr(native_subject, "download_file", lambda key, path: shutil.copyfile(damaged_video, path))
    monkeypatch.setattr(platform, "system", lambda: "Darwin" if native else "Linux")
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "transformers.models.sam3_video.modeling_sam3_video",
        SimpleNamespace(Sam3VideoInferenceSession=object),
    )
    monkeypatch.setitem(sys.modules, "labeler.sam3_engine", SimpleNamespace(get_engine=lambda: None))
    original_capture = cv2.VideoCapture
    captures = []

    def capture(path):
        cap = original_capture(path)
        captures.append(cap)
        return cap

    monkeypatch.setattr(native_subject.cv2, "VideoCapture", capture)
    try:
        with pytest.raises(RuntimeError, match="decod"):
            native_subject.run_playground("video", ["camera"])
        assert all(not cap.isOpened() for cap in captures)
    finally:
        for cap in captures:
            cap.release()


def test_unavailable_ffprobe_keeps_explicit_fps_fallback(monkeypatch, tmp_path):
    from lib import video_timing

    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")

    def unavailable(*a, **kw):
        raise FileNotFoundError("ffprobe unavailable")

    monkeypatch.setattr(video_timing.subprocess, "run", unavailable)
    timings = video_timing.probe_frame_timing(str(video))

    assert timings == []
    assert video_timing.frame_timing(15, 30, timings) == (0.5, 1 / 30, "frame_index/fps")


def test_failed_ffprobe_with_decode_diagnostics_is_not_fps_fallback(monkeypatch, tmp_path):
    from lib import video_timing

    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")

    def failed(command, **kw):
        raise subprocess.CalledProcessError(1, command, output="", stderr="Error while decoding video frame")

    monkeypatch.setattr(video_timing.subprocess, "run", failed)

    with pytest.raises(RuntimeError, match="decod"):
        video_timing.probe_frame_timing(str(video))


@pytest.mark.parametrize("frames", [[], [{"best_effort_timestamp_time": "nan"}], [{}]])
def test_invalid_or_missing_source_pts_keeps_fps_fallback(monkeypatch, tmp_path, frames):
    import json

    from lib import video_timing

    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    monkeypatch.setattr(
        video_timing.subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, json.dumps({"frames": frames}), ""),
    )

    assert video_timing.probe_frame_timing(str(video)) == []
