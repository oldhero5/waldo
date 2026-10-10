"""Decoded moving pixels must retain their source timing and geometry."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from lib.inference_engine import Detection
from lib.video_tracker import VideoTracker

pytestmark = pytest.mark.no_auth_bypass


@pytest.fixture
def moving_video(tmp_path):
    def build(*, vfr=False):
        for index in range(12):
            image = np.zeros((48, 80, 3), dtype=np.uint8)
            image[12:22, index * 4 : index * 4 + 8] = 255
            Image.fromarray(image).save(tmp_path / f"{index}.png")
        source = tmp_path / "frames.txt"
        source.write_text(
            "".join(f"file '{index}.png'\nduration {0.12 if index % 2 == 0 else 0.36}\n" for index in range(12))
        )
        video = tmp_path / ("vfr.mp4" if vfr else "cfr.mp4")
        inputs = (
            ["-f", "concat", "-safe", "0", "-i", str(source)]
            if vfr
            else ["-framerate", "30", "-i", str(tmp_path / "%d.png")]
        )
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                *inputs,
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
        probe = json.loads(
            subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames", "-of", "json", str(video)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        return video, [float(frame["best_effort_timestamp_time"]) for frame in probe["frames"]]

    return build


def _box(image):
    rows, columns = np.where(image[:, :, 0] > 200)
    return [float(columns.min()), float(rows.min()), float(columns.max() + 1), float(rows.max() + 1)]


class _PixelModel:
    """Mock inference only; file loading reproduces Ultralytics stride behavior."""

    def track(self, source, **kwargs):
        def result(image):
            bbox = _box(image)
            box = SimpleNamespace(xyxy=np.array([bbox]), cls=np.array([0]), conf=np.array([0.9]), id=None)
            mask = np.array([[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]])
            return SimpleNamespace(
                boxes=[box],
                masks=[SimpleNamespace(xy=[mask])],
                names={0: "moving"},
                orig_img=image,
                orig_shape=image.shape[:2],
            )

        if isinstance(source, np.ndarray):
            return [result(source)]

        def file_results():
            cap = cv2.VideoCapture(str(source))
            try:
                stride = kwargs["vid_stride"]
                index = 0
                while True:
                    ok, image = cap.read()
                    if not ok:
                        break
                    index += 1
                    if index % stride == 0:
                        yield result(image)
            finally:
                cap.release()

        return file_results()


def test_builtin_tracking_frame_index_identifies_the_pixels_actually_assessed(moving_video):
    video, timestamps = moving_video()
    engine = SimpleNamespace(model=_PixelModel(), _half=False, _needs_tiling=lambda *a: False)
    results = VideoTracker(engine=engine).track_video(str(video))
    assert results
    for result in results:
        assert result.detections[0].bbox[0] == pytest.approx(result.frame_index * 4, abs=1)
        assert result.timestamp_s == pytest.approx(timestamps[result.frame_index], abs=1e-5)


@pytest.mark.parametrize("tiled", [False, True])
def test_vfr_tracking_uses_source_pts_and_decoded_coordinate_dimensions(moving_video, tiled):
    video, timestamps = moving_video(vfr=True)
    engine = SimpleNamespace(
        model=_PixelModel(),
        _half=False,
        _needs_tiling=lambda *a: tiled,
        _predict_tiled=lambda image, conf: [Detection("moving", 0, 0.9, _box(image))],
    )
    results = VideoTracker(engine=engine).track_video(str(video))
    assert len(results) == len(timestamps)
    assert [result.timestamp_s for result in results] == pytest.approx(timestamps, abs=1e-5)
    assert all(result.source_width == 80 and result.source_height == 48 for result in results)
    assert all(result.timestamp_method == "source_pts" for result in results)
    assert [result.frame_duration_s for result in results[:-1]] == pytest.approx(np.diff(timestamps))


def test_disabled_dedup_retains_all_frames_without_history_comparisons(monkeypatch, tmp_path):
    import labeler.frame_extractor as subject

    def extract(command, **kwargs):
        for index in range(20):
            Image.new("RGB", (8, 8), "white").save(command[-1] % (index + 1))
        return SimpleNamespace(stderr=b"")

    monkeypatch.setattr(subject.subprocess, "run", extract)

    def unexpected_comparison(*args):
        raise AssertionError("dedup history should not be compared when disabled")

    monkeypatch.setattr(subject.imagehash.ImageHash, "__sub__", unexpected_comparison)
    frames = subject.extract_frames(Path("unused.mp4"), tmp_path, dedup_threshold=-1, use_cache=False)
    assert len(frames) == 20
    assert all(frame.file_path.exists() and frame.phash for frame in frames)


def test_resampled_extraction_marks_timestamps_approximate(moving_video, tmp_path):
    from labeler.frame_extractor import extract_frames

    video, timestamps = moving_video(vfr=True)
    frames = extract_frames(video, tmp_path / "samples", fps=4, dedup_threshold=-1, use_cache=False)
    assert frames
    assert all(frame.timestamp_method == "resampled_ordinal/fps" for frame in frames)
    source_index = round(_box(cv2.imread(str(frames[0].file_path)))[0] / 4)
    # The FPS filter retains its sampling phase; its output ordinal is not
    # falsely presented as the original pixel source PTS.
    assert frames[0].timestamp_s != timestamps[source_index]


def test_native_sam_frames_keep_source_pts_and_display_rotation(monkeypatch, moving_video):
    import sys
    from contextlib import nullcontext

    from tests.test_video_evidence_regressions import _load_subject

    video, timestamps = moving_video(vfr=True)
    rotated = video.with_name("rotated.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(video), "-c", "copy", str(rotated)],
        check=True,
        capture_output=True,
    )
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
    results = subject.process_video_native(str(rotated), ["moving"], sample_fps=100)
    assert [frame["timestamp_s"] for frame in results] == pytest.approx(timestamps, abs=1e-5)
    assert all((frame["width"], frame["height"]) == (48, 80) for frame in results)
    assert all(frame["timestamp_method"] == "source_pts" for frame in results)
    for frame in results:
        assert frame["detections"][0]["polygon"]
        polygon = np.array(frame["detections"][0]["polygon"]).reshape(-1, 2) * [48, 80]
        box = frame["detections"][0]["bbox"]
        assert polygon.min(axis=0) == pytest.approx(box[:2], abs=1)
        assert polygon.max(axis=0) == pytest.approx(box[2:], abs=1)


def test_missing_source_pts_is_explicitly_approximate(monkeypatch, moving_video):
    import lib.video_tracker as subject

    video, _ = moving_video(vfr=True)
    monkeypatch.setattr(subject, "probe_frame_timing", lambda path: [])
    engine = SimpleNamespace(model=_PixelModel(), _half=False, _needs_tiling=lambda *a: False)
    results = VideoTracker(engine=engine).track_video(str(video))
    assert results
    assert all(result.timestamp_method == "frame_index/fps" for result in results)
    assert all(result.source_width == 80 and result.source_height == 48 for result in results)


@pytest.mark.parametrize("fps", [float("nan"), float("inf"), -1])
def test_invalid_source_rate_cannot_produce_plausible_playback_timing(monkeypatch, fps):
    import lib.video_tracker as subject

    capture = SimpleNamespace(
        isOpened=lambda: True,
        get=lambda prop: {
            cv2.CAP_PROP_FPS: fps,
            cv2.CAP_PROP_FRAME_COUNT: 12,
            cv2.CAP_PROP_FRAME_WIDTH: 80,
            cv2.CAP_PROP_FRAME_HEIGHT: 48,
        }[prop],
        release=lambda: None,
    )
    monkeypatch.setattr(subject.cv2, "VideoCapture", lambda path: capture)
    with pytest.raises(ValueError, match="invalid"):
        subject.validate_video("unused.mp4")
