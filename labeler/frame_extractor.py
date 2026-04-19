import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class VideoMeta:
    fps: float
    duration_s: float
    width: int
    height: int
    codec: str
    frame_count: int


@dataclass
class FrameInfo:
    frame_number: int
    timestamp_s: float
    file_path: Path
    phash: str
    width: int
    height: int


def get_video_metadata(video_path: str | Path) -> VideoMeta:
    video_path = Path(video_path)
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    probe = json.loads(result.stdout)

    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    fps_parts = video_stream["r_frame_rate"].split("/")
    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])
    duration_s = float(probe["format"].get("duration", video_stream.get("duration", 0)))
    width = int(video_stream["width"])
    height = int(video_stream["height"])
    codec = video_stream["codec_name"]
    frame_count = int(video_stream.get("nb_frames", int(fps * duration_s)))

    return VideoMeta(
        fps=fps,
        duration_s=duration_s,
        width=width,
        height=height,
        codec=codec,
        frame_count=frame_count,
    )


def extract_frames(
    video_path: str | Path,
    output_dir: str | Path,
    fps: float = 1.0,
    dedup_threshold: int = 8,
) -> list[FrameInfo]:
    """Extract frames from *video_path* at *fps*, deduplicating near-identical frames.

    Results are cached by video signature (size + mtime + duration) under
    ``/tmp/waldo-frame-cache/``.  On a cache hit the costly ffmpeg + phash
    pipeline is skipped entirely.  Cache TTL is 48 hours (purged on import of
    ``lib.frame_cache``).
    """
    from lib.frame_cache import load_cached_frames, save_cached_frames

    video_path = Path(video_path)
    output_dir = Path(output_dir)

    # --- cache lookup ---
    cached = load_cached_frames(video_path)
    if cached is not None:
        # Re-use cached frames; ensure output_dir exists for callers that expect it.
        output_dir.mkdir(parents=True, exist_ok=True)
        return cached

    # --- cache miss: run ffmpeg + phash pipeline ---
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = str(output_dir / "frame_%06d.jpg")
    subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vf",
            f"fps={fps}",
            "-q:v",
            "2",
            pattern,
        ],
        capture_output=True,
        check=True,
    )

    frame_files = sorted(output_dir.glob("frame_*.jpg"))
    frames: list[FrameInfo] = []
    seen_hashes: list[imagehash.ImageHash] = []

    for i, fp in enumerate(frame_files):
        img = Image.open(fp)
        phash = imagehash.phash(img)

        is_duplicate = any(abs(phash - h) <= dedup_threshold for h in seen_hashes)
        if is_duplicate:
            fp.unlink()
            continue

        seen_hashes.append(phash)
        timestamp_s = i / fps
        w, h = img.size

        frames.append(
            FrameInfo(
                frame_number=i,
                timestamp_s=timestamp_s,
                file_path=fp,
                phash=str(phash),
                width=w,
                height=h,
            )
        )

    # --- persist to cache ---
    save_cached_frames(video_path, frames)

    return frames
