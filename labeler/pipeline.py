"""Shared labeling pipeline utilities — frame extraction, conversion, dataset packaging."""

from __future__ import annotations

import copy
import logging
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

from labeler.converters import to_classify, to_detect, to_obb, to_pose, to_segment
from lib.db import Annotation, Frame, LabelingJob
from lib.storage import upload_file

if TYPE_CHECKING:
    from labeler.sam3_engine import SegmentationResult

logger = logging.getLogger(__name__)


def _update_job(session, job: LabelingJob, **kwargs) -> None:
    for k, v in kwargs.items():
        setattr(job, k, copy.deepcopy(v) if k == "processing_summary" else v)
    session.commit()


def get_converter(task_type: str):
    """Return the (masks_to_yolo_*, write_yolo_dataset) functions for a task type."""
    converters = {
        "segment": (to_segment.masks_to_yolo_polygons, to_segment.write_yolo_dataset),
        "detect": (to_detect.masks_to_yolo_bboxes, to_detect.write_yolo_dataset),
        "obb": (to_obb.masks_to_yolo_obb, to_obb.write_yolo_dataset),
        "pose": (to_pose.masks_to_yolo_pose, to_pose.write_yolo_dataset),
    }
    if task_type == "classify":
        return None  # Classification has a different flow
    return converters.get(task_type, converters["segment"])


def convert_and_store(
    session,
    job: LabelingJob,
    seg_results: list[SegmentationResult],
    db_frames: list[Frame],
    frame_infos,
    class_names: list[str],
    tmpdir: Path,
) -> str:
    """Convert segmentation results to YOLO format, store in DB, zip and upload."""
    if not (len(seg_results) == len(db_frames) == len(frame_infos)):
        raise ValueError("Segmentation, database-frame and source-frame list lengths must match")
    for result in seg_results:
        count = len(result.masks)
        if len(result.boxes) != count or len(result.scores) != count:
            raise ValueError("Mask, box and score lengths must match")
        if result.class_indices is not None and len(result.class_indices) != count:
            raise ValueError("Mask and class-index lengths must match")
    task_type = job.task_type or "segment"
    if task_type not in ("classify", "detect", "segment", "obb", "pose"):
        raise ValueError(f"Unsupported task type: {task_type}")
    # Replace only this run's annotations, atomically with the caller's final
    # commit. A failed conversion/upload rolls back to the prior attempt.
    video_ids = {frame.video_id for frame in db_frames}
    prior_frames = session.query(Frame.id).filter(Frame.video_id.in_(video_ids))
    session.query(Annotation).filter(Annotation.job_id == job.id, Annotation.frame_id.in_(prior_frames)).delete(
        synchronize_session=False
    )
    dataset_dir = tmpdir / "dataset"

    if task_type == "classify":
        _convert_classify(session, job, seg_results, db_frames, frame_infos, class_names, dataset_dir)
    else:
        _convert_label_format(session, job, seg_results, db_frames, frame_infos, class_names, dataset_dir, task_type)

    # Zip and upload
    zip_path = tmpdir / "dataset.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in dataset_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(dataset_dir))

    result_key = f"results/{job.id}/dataset.zip"
    upload_file(result_key, zip_path)
    return result_key


def _mask_polygon(mask) -> list[float]:
    height, width = mask.shape
    contours, _ = cv2.findContours(mask.astype("uint8") * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []
    contour = max(contours, key=cv2.contourArea)
    polygon = cv2.approxPolyDP(contour, 0.001 * cv2.arcLength(contour, True), True).reshape(-1, 2)
    if len(polygon) < 3:
        return []
    return [value for x, y in polygon for value in (float(x / width), float(y / height))]


def _store_observation(session, job, result, frame, mask_index, class_names):
    class_index = int(result.class_indices[mask_index]) if result.class_indices is not None else 0
    if not 0 <= class_index < len(class_names):
        raise ValueError(f"Unknown class index: {class_index}")
    height, width = result.masks.shape[1:]
    if not width or not height:
        raise ValueError("Invalid mask dimensions")
    x1, y1, x2, y2 = result.boxes[mask_index]
    track_ids = getattr(result, "track_ids", None)
    annotation = Annotation(
        frame_id=frame.id,
        job_id=job.id,
        class_name=class_names[class_index],
        class_index=class_index,
        polygon=_mask_polygon(result.masks[mask_index]),
        bbox=[
            float((x1 + x2) / 2 / width),
            float((y1 + y2) / 2 / height),
            float((x2 - x1) / width),
            float((y2 - y1) / height),
        ],
        confidence=float(result.scores[mask_index]),
        track_id=int(track_ids[mask_index]) if track_ids is not None else None,
    )
    session.add(annotation)
    return annotation


def replace_raw_observations(session, job, results, frames, class_names):
    """Commit-ready evidence for successful clips, independently of dataset export."""
    if len(results) != len(frames):
        raise ValueError("Segmentation and database-frame list lengths must match")
    for result in results:
        count = len(result.masks)
        if len(result.boxes) != count or len(result.scores) != count:
            raise ValueError("Mask, box and score lengths must match")
        if result.class_indices is not None and len(result.class_indices) != count:
            raise ValueError("Mask and class-index lengths must match")
    source_frames = session.query(Frame.id).filter(Frame.video_id.in_({frame.video_id for frame in frames}))
    session.query(Annotation).filter(
        Annotation.job_id == job.id,
        Annotation.frame_id.in_(source_frames),
    ).delete(synchronize_session=False)
    for result, frame in zip(results, frames, strict=True):
        for index in range(len(result.masks)):
            _store_observation(session, job, result, frame, index, class_names)


def _convert_label_format(session, job, seg_results, db_frames, frame_infos, class_names, dataset_dir, task_type):
    """Store raw sightings separately from task-specific export geometry."""
    masks_to_yolo, write_dataset = get_converter(task_type)
    frame_paths, annotations_per_frame, group_ids = [], [], []
    for result, frame, info in zip(seg_results, db_frames, frame_infos, strict=True):
        lines = []
        complete = True
        for mask_index in range(len(result.masks)):
            annotation = _store_observation(session, job, result, frame, mask_index, class_names)
            converted = masks_to_yolo(result.masks[mask_index : mask_index + 1], [annotation.class_index], min_area=0)
            if task_type == "detect" and not converted:
                converted = [str(annotation.class_index) + " " + " ".join(f"{value:.6f}" for value in annotation.bbox)]
            if not converted:
                complete = False
            lines.extend(converted)
        if complete:
            frame_paths.append(info.file_path)
            annotations_per_frame.append(lines)
            group_ids.append(str(frame.video_id))
        else:
            logger.warning("Skipping %s export image with unrepresentable sighting: %s", task_type, info.file_path)
        job.processed_frames = result.frame_index + 1
        job.progress = (result.frame_index + 1) / len(frame_infos)
    write_dataset(dataset_dir, frame_paths, annotations_per_frame, class_names, group_ids=group_ids)


def _convert_classify(session, job, seg_results, db_frames, frame_infos, class_names, dataset_dir):
    """All classification crops inherit their source video's split."""
    crops_per_frame = []
    for result, frame, info in zip(seg_results, db_frames, frame_infos, strict=True):
        image = cv2.imread(str(info.file_path))
        if image is None:
            raise ValueError(f"Cannot decode source frame: {info.file_path}")
        classes = []
        for mask_index in range(len(result.masks)):
            annotation = _store_observation(session, job, result, frame, mask_index, class_names)
            classes.append(annotation.class_index)
        crops_per_frame.append(to_classify.masks_to_crops(result.masks, image, class_names, classes, min_area=0))
        job.processed_frames = result.frame_index + 1
        job.progress = (result.frame_index + 1) / len(frame_infos)
    to_classify.write_yolo_dataset(
        dataset_dir,
        [info.file_path for info in frame_infos],
        crops_per_frame,
        class_names,
        group_ids=[str(frame.video_id) for frame in db_frames],
    )
