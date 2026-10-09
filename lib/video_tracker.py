"""Video object tracker — optimized with frame skipping and batched inference."""

import logging
import math

import cv2

from lib.config import settings
from lib.inference_engine import Detection, FrameResult, get_engine
from lib.video_timing import frame_timing, probe_frame_timing

logger = logging.getLogger(__name__)

# Process at most this many FPS — skip intermediate frames
MAX_PROCESSING_FPS = 8


def validate_video(path: str) -> dict:
    """Verify video can be opened and return metadata."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    if frame_count <= 0 or not math.isfinite(fps) or fps <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"Video has no frames or invalid FPS: {path}")

    return {"fps": fps, "frame_count": frame_count, "width": width, "height": height}


def _center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _bbox_diag(bbox: list[float]) -> float:
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return math.sqrt(w * w + h * h)


class CentroidTracker:
    """Center-distance tracker — robust for small objects."""

    def __init__(self, max_distance: float = 100.0, max_lost: int = 15):
        self.max_distance = max_distance
        self.max_lost = max_lost
        self.next_id = 1
        self.tracks: dict[int, dict] = {}

    def update(self, detections: list[Detection]) -> list[Detection]:
        if not detections:
            lost = []
            for tid, t in self.tracks.items():
                t["lost"] += 1
                if t["lost"] > self.max_lost:
                    lost.append(tid)
            for tid in lost:
                del self.tracks[tid]
            return detections

        det_centers = [_center(d.bbox) for d in detections]

        used_tracks = set()
        used_dets = set()

        # Build distance pairs
        pairs = []
        for di, (dx, dy) in enumerate(det_centers):
            for tid, t in self.tracks.items():
                dist = math.sqrt((dx - t["cx"]) ** 2 + (dy - t["cy"]) ** 2)
                effective_max = max(self.max_distance, _bbox_diag(t["bbox"]) * 2)
                if dist < effective_max:
                    pairs.append((dist, di, tid))

        pairs.sort()

        for dist, di, tid in pairs:
            if di in used_dets or tid in used_tracks:
                continue
            detections[di].track_id = tid
            used_dets.add(di)
            used_tracks.add(tid)
            cx, cy = det_centers[di]
            self.tracks[tid] = {
                "cx": cx,
                "cy": cy,
                "bbox": detections[di].bbox,
                "lost": 0,
                "class_index": detections[di].class_index,
            }

        for di in range(len(detections)):
            if di not in used_dets:
                tid = self.next_id
                self.next_id += 1
                detections[di].track_id = tid
                cx, cy = det_centers[di]
                self.tracks[tid] = {
                    "cx": cx,
                    "cy": cy,
                    "bbox": detections[di].bbox,
                    "lost": 0,
                    "class_index": detections[di].class_index,
                }

        lost = []
        for tid in self.tracks:
            if tid not in used_tracks:
                self.tracks[tid]["lost"] += 1
                if self.tracks[tid]["lost"] > self.max_lost:
                    lost.append(tid)
        for tid in lost:
            del self.tracks[tid]

        return detections


class VideoTracker:
    def __init__(self, conf: float = 0.25, tracker: str = "bytetrack.yaml", *, engine=None):
        self.conf = conf
        self.tracker = tracker
        self.engine = engine

    def track_video(
        self,
        path: str,
        on_frame: "callable | None" = None,
    ) -> list[FrameResult]:
        """Track objects across video frames with frame skipping for speed."""
        meta = validate_video(path)
        meta["timings"] = probe_frame_timing(path)
        engine = self.engine if self.engine is not None else get_engine()

        needs_tiling = engine._needs_tiling(meta["height"], meta["width"])

        if needs_tiling:
            logger.info(
                "Video is %dx%d — using tiled inference + centroid tracking",
                meta["width"],
                meta["height"],
            )
            return self._track_with_tiling(path, meta, engine, on_frame)
        else:
            return self._track_with_builtin(path, meta, engine, on_frame)

    def _compute_frame_skip(self, fps: float) -> int:
        """Compute how many frames to skip between processed frames."""
        if fps <= MAX_PROCESSING_FPS:
            return 1  # Process every frame
        return max(1, int(fps / MAX_PROCESSING_FPS))

    def _track_with_tiling(self, path, meta, engine, on_frame):
        """Tiled inference per frame + centroid tracking for large videos."""
        cap = cv2.VideoCapture(path)
        tracker = CentroidTracker()
        frame_results = []
        frame_skip = self._compute_frame_skip(meta["fps"])

        if frame_skip > 1:
            logger.info(
                "Frame skip: processing every %d frames (%.1f→%.1f fps)",
                frame_skip,
                meta["fps"],
                meta["fps"] / frame_skip,
            )

        try:
            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # Skip frames for speed
                if frame_idx % frame_skip != 0:
                    frame_idx += 1
                    continue

                timestamp_s, duration_s, method = frame_timing(frame_idx, meta["fps"], meta.get("timings", []))
                detections = engine._predict_tiled(frame, self.conf)
                detections = tracker.update(detections)

                fr = FrameResult(
                    frame_index=frame_idx,
                    timestamp_s=timestamp_s,
                    detections=detections,
                    source_width=frame.shape[1],
                    source_height=frame.shape[0],
                    frame_duration_s=duration_s,
                    timestamp_method=method,
                )
                frame_results.append(fr)

                if on_frame:
                    on_frame(fr)

                frame_idx += 1

        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "MPS" in str(e):
                engine._clear_device_cache()
                return frame_results
            raise
        finally:
            cap.release()

        # A brief sighting is still evidence, even in a long clip.
        return frame_results

    def _track_with_builtin(self, path, meta, engine, on_frame):
        """Use Ultralytics built-in model.track() for normal-resolution videos."""
        frame_skip = self._compute_frame_skip(meta["fps"])
        cap = cv2.VideoCapture(path)
        # In-memory sources preserve tracking state, so reset it once per clip.
        for tracker in getattr(getattr(engine.model, "predictor", None), "trackers", []):
            tracker.reset()
        frame_results = []
        try:
            frame_idx = -1
            while True:
                decoded, frame = cap.read()
                if not decoded:
                    break
                frame_idx += 1
                # Preserve the file loader's existing stride phase, and also
                # assess the first frame. Never infer source index from result ordinal.
                if frame_idx != 0 and (frame_idx + 1) % frame_skip:
                    continue
                results = engine.model.track(
                    source=frame,
                    conf=self.conf,
                    tracker=self.tracker,
                    device=settings.device,
                    half=engine._half,
                    persist=True,
                    verbose=False,
                )
                if len(results) != 1:
                    raise ValueError("Tracking must return exactly one result per decoded source frame")
                result = results[0]
                timestamp_s, duration_s, method = frame_timing(frame_idx, meta["fps"], meta.get("timings", []))
                detections = []
                names = result.names

                if result.boxes is not None:
                    for i, box in enumerate(result.boxes):
                        track_id = None
                        if box.id is not None:
                            track_id = int(box.id[0])

                        det = Detection(
                            class_name=names[int(box.cls[0])],
                            class_index=int(box.cls[0]),
                            confidence=float(box.conf[0]),
                            bbox=[float(x) for x in box.xyxy[0].tolist()],
                            track_id=track_id,
                        )
                        if result.masks is not None and i < len(result.masks):
                            seg = result.masks[i].xy[0]
                            det.mask = [[float(x), float(y)] for x, y in seg]
                        detections.append(det)

                fr = FrameResult(
                    frame_index=frame_idx,
                    timestamp_s=timestamp_s,
                    detections=detections,
                    source_width=frame.shape[1],
                    source_height=frame.shape[0],
                    frame_duration_s=duration_s,
                    timestamp_method=method,
                )
                frame_results.append(fr)

                if on_frame:
                    on_frame(fr)
        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "MPS" in str(e):
                engine._clear_device_cache()
                return frame_results
            raise
        finally:
            cap.release()

        return frame_results
