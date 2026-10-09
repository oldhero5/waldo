"""Source presentation timing shared by decoded video inference paths."""

import json
import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameTiming:
    timestamp_s: float
    duration_s: float | None


def probe_frame_timing(path: str) -> list[FrameTiming]:
    """Return decoded presentation times relative to the container start.

    FFprobe and OpenCV both enumerate decoded frames in presentation order.
    Missing/invalid timing stays an explicitly marked FPS approximation.
    """
    if not Path(path).is_file():
        return []
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_format",
                "-show_entries",
                "frame=best_effort_timestamp_time,duration_time,pkt_duration_time:format=start_time",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        probe = json.loads(result.stdout)
        frames = probe["frames"]
        times = [float(frame["best_effort_timestamp_time"]) for frame in frames]
        if not times or not all(math.isfinite(time) for time in times):
            raise ValueError("Missing source presentation times")
        if any(right <= left for left, right in zip(times, times[1:])):
            raise ValueError("Non-increasing source presentation times")
        origin = float(probe.get("format", {}).get("start_time", times[0]))
        if not math.isfinite(origin):
            raise ValueError("Invalid container start time")
        timings = []
        for index, (frame, time) in enumerate(zip(frames, times)):
            duration = (
                times[index + 1] - time
                if index + 1 < len(times)
                else float(frame.get("duration_time", frame.get("pkt_duration_time", 0)))
            )
            timings.append(FrameTiming(time - origin, duration if math.isfinite(duration) and duration > 0 else None))
        return timings
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as error:
        logger.warning("Source PTS unavailable for %s; using FPS approximation: %s", path, error)
        return []


def frame_timing(index: int, fps: float, timings: list[FrameTiming]) -> tuple[float, float | None, str]:
    if index < len(timings):
        timing = timings[index]
        return timing.timestamp_s, timing.duration_s, "source_pts"
    return index / fps, 1 / fps, "frame_index/fps"
