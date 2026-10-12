"""Probe regression using real CPU tensors/hooks and the locked AutoBackend warmup.

CUDA device flags and hardware calls are emulated here. These tests do not
qualify an NVIDIA host, the checkpoint, or actual CUDA inference.
"""

import hashlib
import importlib.util
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

SPEC = importlib.util.spec_from_file_location("release_cuda_probe", Path(__file__).with_name("release_cuda_probe.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture
def probe_harness(tmp_path, monkeypatch):
    import ultralytics
    from ultralytics.nn.autobackend import AutoBackend

    from lib import inference_engine
    from lib.config import settings

    weights = tmp_path / "test-checkpoint.pt"
    weights.write_bytes(bytes(5613764))
    monkeypatch.setattr(probe, "CHECKPOINT", hashlib.sha256(weights.read_bytes()).hexdigest())
    monkeypatch.setattr(settings, "device", "cuda:0")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "synchronize", lambda: None)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda index: "emulated CUDA flags on CPU")
    monkeypatch.setattr(torch.Tensor, "is_cuda", property(lambda tensor: True))
    monkeypatch.setattr(probe.subprocess, "check_output", lambda *args, **kwargs: "emulated driver")
    for name in ("RELEASE_ROOT_DIGEST", "RELEASE_RUNNABLE_DIGEST", "RELEASE_ARCHIVE_SHA256", "GITHUB_SHA"):
        monkeypatch.setenv(name, "test-only identity")

    # torch.empty permits arbitrary uninitialized values. Make legal NaNs
    # deterministic so the real upstream warmup invokes hooks with that input.
    original_empty = torch.empty

    def nan_empty(*args, **kwargs):
        return original_empty(*args, **kwargs).fill_(float("nan"))

    monkeypatch.setattr(torch, "empty", nan_empty)
    engines = []
    original_engine = inference_engine.InferenceEngine

    def tracked_engine():
        engine = original_engine()
        engines.append(engine)
        return engine

    monkeypatch.setattr(inference_engine, "InferenceEngine", tracked_engine)

    def execute(*, finite_measured):
        class Network(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.weight = torch.nn.Parameter(torch.ones(1, dtype=torch.float16))

            def forward(self, image):
                if finite_measured:
                    return image * self.weight
                return image * float("nan")

        class SyntheticYOLO:
            def __init__(self, path):
                self.model = Network()
                self.overrides = {}
                self.initialized = False

            def __call__(self, image, *, conf, device, half, verbose):
                assert device == "cuda:0" and half is True and conf == 0.25
                if not self.initialized:
                    backend = SimpleNamespace(
                        format="triton", device=torch.device("cpu"), fp16=True, forward=self.model
                    )
                    AutoBackend.warmup(backend)
                    self.initialized = True
                tensor = torch.from_numpy(image.transpose(2, 0, 1).copy()).unsqueeze(0).to(torch.float16)
                self.model(tensor)
                # Empty detections are valid; raw NaNs must still fail the probe.
                return [SimpleNamespace(names={0: "synthetic"}, boxes=[], masks=None)]

        monkeypatch.setattr(ultralytics, "YOLO", SyntheticYOLO)
        return probe.run(weights)

    try:
        yield execute
    finally:
        for engine in engines:
            shutil.rmtree(engine._weights_cache)


def test_nonfinite_backend_warmup_does_not_reject_finite_fixed_image_pass(probe_harness):
    report = probe_harness(finite_measured=True)
    assert report["finite_outputs"] is True
    assert report["cuda_forward"] is True and report["cuda_parameters"] is True
    assert report["detection_count"] == 0 and report["elapsed_seconds"] > 0
    assert report["input_sha256"] == hashlib.sha256(np.zeros((640, 640, 3), dtype=np.uint8).tobytes()).hexdigest()


def test_nonfinite_fixed_image_forward_still_fails_with_empty_detections(probe_harness):
    with pytest.raises(RuntimeError, match="CUDA/finite checks"):
        probe_harness(finite_measured=False)
