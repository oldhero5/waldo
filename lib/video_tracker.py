"""Video object tracker — optimized with frame skipping and batched inference."""
import logging
import math

import cv2

from lib.config import settings
from lib.inference_engine import Detection, FrameResult, get_engine

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

    if frame_count == 0 or fps == 0:
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
                "cx": cx, "cy": cy,
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
                    "cx": cx, "cy": cy,
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
    def __init__(self, conf: float = 0.25, tracker: str = "bytetrack.yaml"):
        self.conf = conf
        self.tracker = tracker

    def track_video(
        self,
        path: str,
        on_frame: "callable | None" = None,
    ) -> list[FrameResult]:
        """Track objects across video frames with frame skipping for speed."""
        meta = validate_video(path)
        engine = get_engine()
        engine._ensure_loaded()

        needs_tiling = engine._needs_tiling(meta["height"], meta["width"])

        if needs_tiling:
            logger.info(
                "Video is %dx%d — using tiled inference + centroid tracking",
                meta["width"], meta["height"],
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
            logger.info("Frame skip: processing every %d frames (%.1f→%.1f fps)",
                        frame_skip, meta["fps"], meta["fps"] / frame_skip)

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

                timestamp_s = frame_idx / meta["fps"] if meta["fps"] > 0 else 0.0
                detections = engine._predict_tiled(frame, self.conf)
                detections = tracker.update(detections)

                fr = FrameResult(
                    frame_index=frame_idx,
                    timestamp_s=round(timestamp_s, 4),
                    detections=detections,
                )
                frame_results.append(fr)

                if on_frame:
                    on_frame(fr)

                frame_idx += 1

        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "MPS" in str(e):
                engine._clear_device_cache()
                return self._filter_transient_tracks(frame_results, meta)
            raise
        finally:
            cap.release()

        return self._filter_transient_tracks(frame_results, meta)

    def _filter_transient_tracks(self, frame_results, meta):
        """Remove tracks that appear too briefly (likely false positives)."""
        track_counts: dict[int, int] = {}
        for fr in frame_results:
            for d in fr.detections:
                if d.track_id is not None:
                    track_counts[d.track_id] = track_counts.get(d.track_id, 0) + 1

        total_frames = len(frame_results)
        min_frames = max(3, int(total_frames * 0.05))
        valid_tracks = {tid for tid, count in track_counts.items() if count >= min_frames}

        logger.info("Track filter: %d/%d tracks kept (min_frames=%d)",
                     len(valid_tracks), len(track_counts), min_frames)

        for fr in frame_results:
            fr.detections = [d for d in fr.detections if d.track_id in valid_tracks]

        return frame_results

    def _track_with_builtin(self, path, meta, engine, on_frame):
        """Use Ultralytics built-in model.track() for normal-resolution videos."""
        # Use frame skipping via vid_stride for speed
        frame_skip = self._compute_frame_skip(meta["fps"])

        results_gen = engine.model.track(
            source=path,
            conf=self.conf,
            tracker=self.tracker,
            device=settings.device,
            half=engine._half,
            persist=True,
            stream=True,
            verbose=False,
            vid_stride=frame_skip,
        )

        frame_results = []
        try:
            for result_idx, result in enumerate(results_gen):
                frame_idx = result_idx * frame_skip
                timestamp_s = frame_idx / meta["fps"] if meta["fps"] > 0 else 0.0
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
                    timestamp_s=round(timestamp_s, 4),
                    detections=detections,
                )
                frame_results.append(fr)

                if on_frame:
                    on_frame(fr)
        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "MPS" in str(e):
                engine._clear_device_cache()
                return frame_results
            raise

        return frame_results
