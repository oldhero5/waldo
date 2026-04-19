"""Frame-extraction cache backed by /tmp/waldo-frame-cache/.

Public API
----------
load_cached_frames(video_path) -> list[FrameInfo] | None
    Return cached FrameInfo list if all files are present; None otherwise.

save_cached_frames(video_path, frames)
    Persist the frame list to the cache index for *video_path*.

On first import the module purges cache directories older than 48 hours
(best-effort — errors are silently ignored so startup is never blocked).
"""

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_ROOT = Path("/tmp/waldo-frame-cache")  # noqa: S108
_TTL_SECONDS = 48 * 3600  # 48 hours


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _video_signature(video_path: Path) -> str:
    """Return a stable, cheap hash that identifies a specific video file.

    Uses file size + mtime + ffprobe duration (rounded to ms) so it detects
    in-place overwrites without reading the full file content.
    """
    stat = video_path.stat()
    size = stat.st_size
    mtime = stat.st_mtime

    # Use ffprobe duration for extra stability across filesystem copies
    duration_str = "unknown"
    try:
        import subprocess

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            probe = json.loads(result.stdout)
            duration_str = probe.get("format", {}).get("duration", "unknown")
    except Exception:
        pass  # fall back to size+mtime only — still good enough

    raw = f"{video_path.name}:{size}:{mtime:.3f}:{duration_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_dir(sig: str) -> Path:
    return _CACHE_ROOT / sig


def _index_path(sig: str) -> Path:
    return _cache_dir(sig) / "index.json"


def _purge_old_cache_dirs() -> None:
    """Remove cache dirs that are older than _TTL_SECONDS.  Best-effort only."""
    if not _CACHE_ROOT.exists():
        return
    cutoff = time.time() - _TTL_SECONDS
    try:
        for entry in _CACHE_ROOT.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    logger.debug("Frame cache: purged expired dir %s", entry.name)
            except Exception:
                pass
    except Exception:
        pass


# Purge on import — fire-and-forget, never raises
_purge_old_cache_dirs()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_cached_frames(video_path: "str | Path") -> "list | None":
    """Load cached FrameInfo objects for *video_path*.

    Returns a list of FrameInfo dataclass instances if the cache is warm and
    all referenced files still exist on disk; otherwise returns None.

    Import of FrameInfo is deferred to avoid a circular dependency
    (frame_extractor imports this module).
    """
    from labeler.frame_extractor import FrameInfo  # deferred import

    video_path = Path(video_path)
    try:
        sig = _video_signature(video_path)
        idx = _index_path(sig)
        if not idx.exists():
            return None

        with idx.open() as f:
            data = json.load(f)

        frames = []
        for entry in data:
            fp = Path(entry["file_path"])
            if not fp.exists():
                logger.debug("Frame cache miss: file missing %s", fp)
                return None  # cache partially invalid — force re-extraction
            frames.append(
                FrameInfo(
                    frame_number=entry["frame_number"],
                    timestamp_s=entry["timestamp_s"],
                    file_path=fp,
                    phash=entry["phash"],
                    width=entry["width"],
                    height=entry["height"],
                )
            )
        logger.info("Frame cache hit: %d frames for %s (sig=%s)", len(frames), video_path.name, sig)
        return frames
    except Exception as e:
        logger.debug("Frame cache load error (ignored): %s", e)
        return None


def save_cached_frames(video_path: "str | Path", frames: list) -> None:
    """Persist *frames* to the cache index for *video_path*.

    Silently swallows all errors so a cache write failure never breaks the
    main pipeline.
    """
    video_path = Path(video_path)
    try:
        sig = _video_signature(video_path)
        cache_dir = _cache_dir(sig)
        cache_dir.mkdir(parents=True, exist_ok=True)

        data = [
            {
                "frame_number": fi.frame_number,
                "timestamp_s": fi.timestamp_s,
                "file_path": str(fi.file_path),
                "phash": fi.phash,
                "width": fi.width,
                "height": fi.height,
            }
            for fi in frames
        ]
        with _index_path(sig).open("w") as f:
            json.dump(data, f)

        logger.info("Frame cache saved: %d frames for %s (sig=%s)", len(frames), video_path.name, sig)
    except Exception as e:
        logger.debug("Frame cache save error (ignored): %s", e)
