#!/usr/bin/env python3
"""Bounded YOLO11 FP16 CUDA probe using Waldo's production inference adapter.

This is actual synthetic-image CUDA inference, not SAM3, bfloat16, training,
accuracy, or codec qualification. Run read-only in the exact CUDA candidate,
with NVIDIA passthrough and no network. Checkpoint is verified before loading.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CHECKPOINT = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
REVISION = "5599de6f3b0ef95f98f37530ed0f3bf149c10b37"


def run(weights):
    path = Path(weights)
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != CHECKPOINT or path.stat().st_size != 5613764:
        raise RuntimeError("YOLO11 checkpoint hash/size mismatch")
    import numpy as np
    import torch
    from ultralytics import YOLO

    from lib.config import settings
    from lib.inference_engine import InferenceEngine

    if not torch.cuda.is_available() or settings.device != "cuda:0":
        raise RuntimeError("Actual NVIDIA CUDA device is required")
    model = YOLO(str(path))
    model.overrides["imgsz"] = 640
    image = np.zeros((640, 640, 3), dtype=np.uint8)
    observed = []
    finite_forwards = []

    def observe(module, args):
        tensor = args[0]
        observed.append(tensor.is_cuda and tensor.dtype == torch.float16)

    hook = model.model.register_forward_pre_hook(observe)

    def finite_tensors(value):
        if isinstance(value, torch.Tensor):
            return [bool(torch.isfinite(value).all().item())]
        if isinstance(value, (list, tuple)):
            return [result for child in value for result in finite_tensors(child)]
        if isinstance(value, dict):
            return [result for child in value.values() for result in finite_tensors(child)]
        return []

    def observe_outputs(module, args, output):
        checks = finite_tensors(output)
        finite_forwards.append(bool(checks) and all(checks))

    output_hook = model.model.register_forward_hook(observe_outputs)
    adapter = InferenceEngine()
    adapter.model = model
    adapter._half = True
    torch.cuda.synchronize()
    started = time.perf_counter()
    detections = adapter.predict_image(image, conf=0.25)
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    hook.remove()
    output_hook.remove()
    cuda_parameters = all(
        parameter.is_cuda and parameter.dtype == torch.float16 for parameter in model.model.parameters()
    )
    finite = (
        bool(finite_forwards)
        and all(finite_forwards)
        and all(np.isfinite([item.confidence, *item.bbox]).all() for item in detections)
    )
    if not observed or not all(observed) or not cuda_parameters or not finite:
        raise RuntimeError("Forward pass, parameters or outputs failed CUDA/finite checks")
    hardware = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,driver_version,uuid", "--format=csv,noheader"], text=True
    ).strip()  # noqa: S603,S607
    return {
        "checkpoint_sha256": actual,
        "checkpoint_revision": REVISION,
        "checkpoint": "yolo11n.pt",
        "checkpoint_bytes": path.stat().st_size,
        "dtype": "float16",
        "cuda_forward": True,
        "cuda_parameters": cuda_parameters,
        "finite_outputs": finite,
        "detection_count": len(detections),
        "input_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        "input": "640x640x3 uint8 zeros RGB",
        "parameters": {"device": "cuda:0", "half": True, "conf": 0.25, "imgsz": 640},
        "elapsed_seconds": elapsed,
        "gpu": torch.cuda.get_device_name(0),
        "driver": hardware,
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "candidate": {
            key: os.environ[key]
            for key in ("RELEASE_ROOT_DIGEST", "RELEASE_RUNNABLE_DIGEST", "RELEASE_ARCHIVE_SHA256", "GITHUB_SHA")
        },
    }


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(json.dumps(run(sys.argv[1]), indent=2) + "\n")
