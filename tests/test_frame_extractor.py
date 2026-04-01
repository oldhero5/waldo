import subprocess
import tempfile
from pathlib import Path

import pytest

from labeler.frame_extractor import extract_frames, get_video_metadata


@pytest.fixture
def synthetic_clip():
    """Generate a small synthetic video with ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        path = f.name
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            "color=c=blue:size=320x240:rate=10:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            path,
        ],
        capture_output=True,
        check=True,
    )
    yield Path(path)
    Path(path).unlink(missing_ok=True)


def test_get_video_metadata(synthetic_clip):
    meta = get_video_metadata(synthetic_clip)
    assert meta.width == 320
    assert meta.height == 240
    assert meta.fps == pytest.approx(10.0, abs=0.1)
    assert meta.duration_s == pytest.approx(3.0, abs=0.5)


def test_extract_frames(synthetic_clip):
    with tempfile.TemporaryDirectory() as tmpdir:
        frames = extract_frames(synthetic_clip, tmpdir, fps=1.0, dedup_threshold=8)
        # 3 second clip at 1fps should give ~3 frames, but dedup may reduce
        # A solid color video will dedup heavily — expect at least 1 frame
        assert len(frames) >= 1
        for f in frames:
            assert f.file_path.exists()
            assert f.width == 320
            assert f.height == 240
            assert f.phash
