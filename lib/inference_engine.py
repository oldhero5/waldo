"""Multi-model YOLO inference pool for serving predictions — optimized for speed."""

import logging
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from lib.config import settings
from lib.db import ModelRegistry, SessionLocal
from lib.storage import download_file

logger = logging.getLogger(__name__)

_pool: "ModelPool | None" = None
_pool_lock = threading.Lock()

# Only tile when the image is much larger than training size.
# 1080p (2M px) / 640² (0.41M px) = ~5x — still fine without tiling.
# 4K (8.3M px) / 640² = ~20x — needs tiling.
TILE_AREA_RATIO_THRESHOLD = 8

DEFAULT_TRAIN_IMGSZ = 640

# Memory pressure threshold: evict LRU when MPS allocation exceeds this fraction.
MPS_MEMORY_PRESSURE_THRESHOLD = 0.80


@dataclass
class Detection:
    class_name: str
    class_index: int
    confidence: float
    bbox: list[float]  # [x1, y1, x2, y2] in pixels
    track_id: int | None = None
    mask: list[list[float]] | None = None


@dataclass
class FrameResult:
    frame_index: int
    timestamp_s: float
    detections: list[Detection] = field(default_factory=list)


def _nms_torch(detections: list[Detection], iou_threshold: float = 0.5) -> list[Detection]:
    """Fast NMS using torchvision.ops.nms — O(n log n) instead of O(n²)."""
    if not detections:
        return []

    import torch

    try:
        from torchvision.ops import nms
    except ImportError:
        return _nms_python(detections, iou_threshold)

    boxes = torch.tensor([d.bbox for d in detections], dtype=torch.float32)
    scores = torch.tensor([d.confidence for d in detections], dtype=torch.float32)

    keep_indices = nms(boxes, scores, iou_threshold)
    return [detections[i] for i in keep_indices.tolist()]


def _nms_python(detections: list[Detection], iou_threshold: float = 0.5) -> list[Detection]:
    """Fallback Python NMS."""
    if not detections:
        return []
    detections.sort(key=lambda d: d.confidence, reverse=True)
    keep = []
    for d in detections:
        is_dup = False
        for k in keep:
            # Fast IoU
            x1 = max(d.bbox[0], k.bbox[0])
            y1 = max(d.bbox[1], k.bbox[1])
            x2 = min(d.bbox[2], k.bbox[2])
            y2 = min(d.bbox[3], k.bbox[3])
            inter = max(0, x2 - x1) * max(0, y2 - y1)
            area_d = (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1])
            area_k = (k.bbox[2] - k.bbox[0]) * (k.bbox[3] - k.bbox[1])
            if inter / (area_d + area_k - inter + 1e-6) > iou_threshold:
                is_dup = True
                break
        if not is_dup:
            keep.append(d)
    return keep


# Use torch NMS by default
_nms = _nms_torch


class InferenceEngine:
    def __init__(self):
        self.model = None
        self.model_id: str | None = None
        self.model_info: dict = {}
        self._weights_cache: Path = Path(tempfile.mkdtemp(prefix="waldo_inference_"))
        self._half: bool = False  # FP16 inference

    def _load_model(self, model_id: str) -> None:
        """Download weights from MinIO and load YOLO model."""
        from ultralytics import YOLO

        session = SessionLocal()
        try:
            entry = session.query(ModelRegistry).filter_by(id=model_id).one()
            weights_path = self._weights_cache / f"{model_id}.pt"

            if not weights_path.exists():
                download_file(entry.weights_minio_key, weights_path)

            self.model = YOLO(str(weights_path))
            self.model_id = model_id

            # Enable FP16 on GPU devices for ~2x speedup
            device = settings.device
            if device in ("mps", "cuda") or (isinstance(device, int)):
                self._half = True
                # Warm up the model with a dummy inference to compile/cache kernels
                dummy = np.zeros((640, 640, 3), dtype=np.uint8)
                self.model(dummy, device=device, half=self._half, verbose=False)
                logger.info("Model warmed up with FP16 on %s", device)
            else:
                self._half = False

            # Read class names
            model_class_names = None
            if hasattr(self.model, "names") and self.model.names:
                names = self.model.names
                if isinstance(names, dict):
                    model_class_names = [names[k] for k in sorted(names.keys())]
                elif isinstance(names, list):
                    model_class_names = names

            self.model_info = {
                "model_id": str(entry.id),
                "name": entry.name,
                "task_type": entry.task_type,
                "model_variant": entry.model_variant,
                "device": settings.device,
                "class_names": model_class_names or (entry.class_names if hasattr(entry, "class_names") else None),
            }
        finally:
            session.close()

    def reload(self, model_id: str) -> None:
        self._clear_device_cache()
        self.model = None
        self.model_id = None
        self._load_model(model_id)

    @property
    def model_name(self) -> str | None:
        return self.model_info.get("name")

    @property
    def device(self) -> str:
        return self.model_info.get("device", settings.device)

    def _needs_tiling(self, img_h: int, img_w: int) -> bool:
        img_area = img_h * img_w
        train_area = DEFAULT_TRAIN_IMGSZ**2
        return (img_area / train_area) > TILE_AREA_RATIO_THRESHOLD

    def _predict_tiled(
        self, image, conf: float, tile_size: int = 640, overlap: int = 128, class_filter: list[str] | None = None
    ) -> list[Detection]:
        """Run inference on overlapping tiles with BATCHED GPU calls."""
        h, w = image.shape[:2]
        stride = tile_size - overlap

        # Collect all tiles and their offsets
        tiles = []
        offsets = []
        for ty in range(0, h, stride):
            for tx in range(0, w, stride):
                y2 = min(ty + tile_size, h)
                x2 = min(tx + tile_size, w)
                y1 = max(0, y2 - tile_size)
                x1 = max(0, x2 - tile_size)
                tile = image[y1:y2, x1:x2]
                if tile.shape[0] < 32 or tile.shape[1] < 32:
                    continue
                tiles.append(tile)
                offsets.append((x1, y1))

        if not tiles:
            return []

        # Run batched inference — process all tiles in one call
        # YOLO accepts a list of images and returns a list of Results
        batch_size = min(len(tiles), 16)  # Cap batch to avoid OOM
        all_dets = []

        for batch_start in range(0, len(tiles), batch_size):
            batch_tiles = tiles[batch_start : batch_start + batch_size]
            batch_offsets = offsets[batch_start : batch_start + batch_size]

            results_list = self.model(
                batch_tiles,
                conf=conf,
                device=settings.device,
                half=self._half,
                verbose=False,
            )

            for result, (ox, oy) in zip(results_list, batch_offsets):
                names = result.names
                for i, box in enumerate(result.boxes):
                    bbox = box.xyxy[0].tolist()
                    bbox[0] += ox
                    bbox[1] += oy
                    bbox[2] += ox
                    bbox[3] += oy

                    det = Detection(
                        class_name=names[int(box.cls[0])],
                        class_index=int(box.cls[0]),
                        confidence=float(box.conf[0]),
                        bbox=[float(x) for x in bbox],
                    )
                    if result.masks is not None and i < len(result.masks):
                        seg = result.masks[i].xy[0]
                        det.mask = [[float(px) + ox, float(py) + oy] for px, py in seg]
                    all_dets.append(det)

        result = _nms(all_dets)
        if class_filter:
            filter_set = set(class_filter)
            result = [d for d in result if d.class_name in filter_set]
        return result

    def predict_image(self, image, conf: float = 0.25, class_filter: list[str] | None = None) -> list[Detection]:
        """Run prediction on a single image. Automatically tiles large images."""
        if self.model is None:
            raise RuntimeError(
                "InferenceEngine has no model loaded. "
                "Use get_engine() or get_pool().get_model(model_id) to obtain a ready engine."
            )

        h, w = image.shape[:2]
        use_tiling = self._needs_tiling(h, w)

        try:
            if use_tiling:
                return self._predict_tiled(image, conf, class_filter=class_filter)
            else:
                return self._predict_single(image, conf, class_filter=class_filter)
        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "MPS" in str(e):
                self._clear_device_cache()
                if use_tiling:
                    return self._predict_tiled(image, conf, class_filter=class_filter)
                else:
                    return self._predict_single(image, conf, class_filter=class_filter)
            raise

    def _predict_single(self, image, conf: float, class_filter: list[str] | None = None) -> list[Detection]:
        """Run prediction on a single image without tiling."""
        results = self.model(image, conf=conf, device=settings.device, half=self._half, verbose=False)
        detections = []
        for result in results:
            names = result.names
            for i, box in enumerate(result.boxes):
                det = Detection(
                    class_name=names[int(box.cls[0])],
                    class_index=int(box.cls[0]),
                    confidence=float(box.conf[0]),
                    bbox=[float(x) for x in box.xyxy[0].tolist()],
                )
                if result.masks is not None and i < len(result.masks):
                    seg = result.masks[i].xy[0]
                    det.mask = [[float(x), float(y)] for x, y in seg]
                detections.append(det)
        if class_filter:
            filter_set = set(class_filter)
            detections = [d for d in detections if d.class_name in filter_set]
        return detections

    def _clear_device_cache(self) -> None:
        import torch

        # torch.mps.empty_cache() exists as an API on Linux too but raises
        # RuntimeError when no MPS backend is present — gate on is_available.
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


class ModelPool:
    """Manages a pool of loaded InferenceEngine instances keyed by model_id.

    LRU eviction keeps the pool bounded to ``max_models``.  Memory pressure on
    MPS is monitored so we don't OOM before hitting the model cap.
    """

    def __init__(self, max_models: int = 3):
        self.max_models = max_models
        self._engines: dict[str, InferenceEngine] = {}
        self._last_used: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_model(self, model_id: str) -> InferenceEngine:
        """Return a loaded engine for *model_id*, loading on demand."""
        with self._lock:
            if model_id in self._engines:
                self._last_used[model_id] = time.monotonic()
                return self._engines[model_id]

            # May need to evict before loading
            self._evict_if_needed()

            engine = InferenceEngine()
            engine._load_model(model_id)
            self._engines[model_id] = engine
            self._last_used[model_id] = time.monotonic()
            logger.info(
                "ModelPool loaded model %s (%s) — pool size %d/%d",
                model_id,
                engine.model_name,
                len(self._engines),
                self.max_models,
            )
            return engine

    def get_active_model(self) -> InferenceEngine:
        """Return the engine for whichever model is marked ``is_active`` in the DB."""
        session = SessionLocal()
        try:
            active = session.query(ModelRegistry).filter_by(is_active=True).first()
            if not active:
                raise RuntimeError("No active model. Activate a model via POST /api/v1/models/{id}/activate")
            model_id = str(active.id)
        finally:
            session.close()
        return self.get_model(model_id)

    def reload_model(self, model_id: str) -> InferenceEngine:
        """Force-reload a model (e.g. after retraining)."""
        with self._lock:
            if model_id in self._engines:
                self._engines[model_id]._clear_device_cache()
                del self._engines[model_id]
                del self._last_used[model_id]
        return self.get_model(model_id)

    @property
    def loaded_model_ids(self) -> list[str]:
        """Return the list of currently-loaded model IDs."""
        with self._lock:
            return list(self._engines.keys())

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._engines)

    # ------------------------------------------------------------------
    # Eviction internals (must be called under self._lock)
    # ------------------------------------------------------------------

    def _active_model_id(self) -> str | None:
        """Look up the current active model ID (DB hit, but cheap)."""
        session = SessionLocal()
        try:
            active = session.query(ModelRegistry).filter_by(is_active=True).first()
            return str(active.id) if active else None
        finally:
            session.close()

    def _evict_if_needed(self) -> None:
        """Evict LRU model(s) if we are at capacity or under memory pressure.

        MUST be called while holding ``self._lock``.
        """
        # Check memory pressure on MPS first — we may need to evict even if
        # we haven't hit max_models yet.
        if self._engines and self._mps_memory_pressure():
            self._evict_lru()

        # Then enforce the hard cap.
        while len(self._engines) >= self.max_models:
            if not self._evict_lru():
                break  # nothing evictable (all models are the active one?!)

    def _evict_lru(self) -> bool:
        """Evict the least-recently-used model that is NOT the active one.

        Returns True if a model was evicted, False otherwise.
        MUST be called while holding ``self._lock``.
        """
        if not self._engines:
            return False

        active_id = self._active_model_id()

        # Sort by last_used ascending (oldest first), skip the active model.
        candidates = [(mid, ts) for mid, ts in self._last_used.items() if mid != active_id]
        if not candidates:
            # Every loaded model is the active one (pool size 1).  Cannot evict.
            return False

        candidates.sort(key=lambda x: x[1])
        victim_id = candidates[0][0]

        engine = self._engines.pop(victim_id)
        self._last_used.pop(victim_id, None)
        engine._clear_device_cache()
        engine.model = None
        logger.info(
            "ModelPool evicted model %s (%s) — pool size %d/%d",
            victim_id,
            engine.model_name,
            len(self._engines),
            self.max_models,
        )
        return True

    @staticmethod
    def _mps_memory_pressure() -> bool:
        """Return True if MPS memory usage exceeds the pressure threshold."""
        if settings.device != "mps":
            return False
        try:
            import torch

            if not hasattr(torch, "mps") or not hasattr(torch.mps, "current_allocated_memory"):
                return False
            allocated = torch.mps.current_allocated_memory()
            # recommended_max_memory not always available — fall back to a
            # conservative 16 GB default (M-series unified memory).
            if hasattr(torch.mps, "recommended_max_memory"):
                total = torch.mps.recommended_max_memory()
            elif hasattr(torch.mps, "driver_allocated_memory"):
                # Rough heuristic: driver memory ~ total available to MPS.
                total = torch.mps.driver_allocated_memory()
            else:
                total = 16 * (1024**3)  # 16 GB fallback
            if total == 0:
                return False
            ratio = allocated / total
            if ratio > MPS_MEMORY_PRESSURE_THRESHOLD:
                logger.warning(
                    "MPS memory pressure: %.1f%% (%d MB / %d MB)",
                    ratio * 100,
                    allocated // (1024**2),
                    total // (1024**2),
                )
                return True
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Module-level accessors
# ---------------------------------------------------------------------------


def get_pool() -> ModelPool:
    """Return the module-level ModelPool singleton (thread-safe)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ModelPool()
    return _pool


def get_engine() -> InferenceEngine:
    """Backward-compatible convenience: return the active model's engine.

    Every existing caller that does ``engine = get_engine()`` continues to work
    identically — the engine is loaded (or cache-hit) via the pool.
    """
    return get_pool().get_active_model()
