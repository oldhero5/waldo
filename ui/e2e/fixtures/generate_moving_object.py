"""Regenerate the lossless 30fps test clip: python3 ui/e2e/fixtures/generate_moving_object.py.

Requires an existing FFmpeg executable. The white rectangle moves four pixels
per decoded frame; fixture inference assesses every ninth frame.
"""

import shutil
import subprocess
from pathlib import Path

width, height = 320, 180
frames = bytearray()
for index in range(60):
    frame = bytearray(width * height * 3)
    x = 20 + index * 4
    for y in range(90, 120):
        offset = (y * width + x) * 3
        frame[offset : offset + 30 * 3] = b"\xff" * (30 * 3)
    frames.extend(frame)
subprocess.run(
    [
        shutil.which("ffmpeg") or "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        "30",
        "-i",
        "pipe:0",
        "-c:v",
        "libvpx-vp9",
        "-lossless",
        "1",
        "-y",
        str(Path(__file__).with_name("moving-object.webm")),
    ],
    input=bytes(frames),
    check=True,
)
