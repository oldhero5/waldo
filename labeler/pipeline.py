"""Shared labeling pipeline utilities — frame extraction, conversion, dataset packaging."""
import zipfile
from pathlib import Path

import cv2

from labeler.converters import to_classify, to_detect, to_obb, to_pose, to_segment
from labeler.sam3_engine import SegmentationResult
from lib.db import Annotation, Frame, LabelingJob
from lib.storage import upload_file


def _update_job(session, job: LabelingJob, **kwargs) -> None:
    for k, v in kwargs.items():
        setattr(job, k, v)
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
    task_type = job.task_type or "segment"
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


def _convert_label_format(session, job, seg_results, db_frames, frame_infos, class_names, dataset_dir, task_type):
    """Convert for segment/detect/obb/pose tasks (label-file based formats)."""
    masks_to_yolo, write_dataset = get_converter(task_type)

    all_annotation_lines: list[list[str]] = []
    frame_paths: list[Path] = []

    class_name = class_names[0] if class_names else "object"

    for seg_result, db_frame, fi in zip(seg_results, db_frames, frame_infos):
        class_indices = (
            seg_result.class_indices.tolist()
            if seg_result.class_indices is not None
            else [0] * seg_result.masks.shape[0]
        )
        num_masks = seg_result.masks.shape[0]
        ann_lines = masks_to_yolo(seg_result.masks, class_indices)
        all_annotation_lines.append(ann_lines)
        frame_paths.append(fi.file_path)

        # Build a mapping from converter output lines back to original mask indices.
        # The converter may filter masks (min_area, contour checks), so ann_lines
        # can be shorter than seg_result.scores. We match by class index + order to
        # pair each surviving line with the correct score.
        # mask_cursor removed — was unused
        line_to_mask_idx: list[int] = []

        # Track which masks survived conversion by re-running class index counts
        surviving_cls = [int(line.split()[0]) for line in ann_lines]
        cls_seen: dict[int, int] = {}
        per_cls_masks: dict[int, list[int]] = {}
        for mi in range(num_masks):
            ci = class_indices[mi] if mi < len(class_indices) else 0
            per_cls_masks.setdefault(ci, []).append(mi)

        for ci in surviving_cls:
            occurrence = cls_seen.get(ci, 0)
            masks_for_cls = per_cls_masks.get(ci, [])
            if occurrence < len(masks_for_cls):
                line_to_mask_idx.append(masks_for_cls[occurrence])
            else:
                line_to_mask_idx.append(0)
            cls_seen[ci] = occurrence + 1

        # Store annotations in DB with correct score alignment
        scores = seg_result.scores
        for line_idx, line in enumerate(ann_lines):
            parts = line.split()
            polygon_coords = [float(x) for x in parts[1:]]
            cls_idx = int(parts[0])

            # Use the correct mask index to get the right score and bbox
            mask_idx = line_to_mask_idx[line_idx] if line_idx < len(line_to_mask_idx) else line_idx
            score = float(scores[mask_idx]) if mask_idx < len(scores) else 1.0

            bbox = None
            if seg_result.boxes.shape[0] > 0 and mask_idx < seg_result.boxes.shape[0]:
                x1, y1, x2, y2 = seg_result.boxes[mask_idx]
                h, w = seg_result.masks.shape[1], seg_result.masks.shape[2]
                if w > 0 and h > 0:
                    bbox = [
                        float((x1 + x2) / 2 / w),
                        float((y1 + y2) / 2 / h),
                        float((x2 - x1) / w),
                        float((y2 - y1) / h),
                    ]

            ann_class_name = class_names[cls_idx] if cls_idx < len(class_names) else class_name

            annotation = Annotation(
                frame_id=db_frame.id,
                job_id=job.id,
                class_name=ann_class_name,
                class_index=cls_idx,
                polygon=polygon_coords,
                bbox=bbox,
                confidence=score,
            )
            session.add(annotation)

        _update_job(
            session, job,
            processed_frames=seg_result.frame_index + 1,
            progress=(seg_result.frame_index + 1) / len(frame_infos),
        )

    write_dataset(dataset_dir, frame_paths, all_annotation_lines, class_names)


def _convert_classify(session, job, seg_results, db_frames, frame_infos, class_names, dataset_dir):
    """Convert for classification task (crop-based format)."""
    class_name = class_names[0] if class_names else "object"
    crops_per_frame = []

    for seg_result, db_frame, fi in zip(seg_results, db_frames, frame_infos):
        frame_img = cv2.imread(str(fi.file_path))
        class_indices = (
            seg_result.class_indices.tolist()
            if seg_result.class_indices is not None
            else [0] * seg_result.masks.shape[0]
        )
        crops = to_classify.masks_to_crops(
            seg_result.masks, frame_img, class_names, class_indices
        )
        crops_per_frame.append(crops)

        # Store annotations in DB (bbox-only for classify)
        for idx in range(seg_result.masks.shape[0]):
            if idx < seg_result.scores.shape[0]:
                score = float(seg_result.scores[idx])
            else:
                score = 1.0

            bbox = None
            if idx < seg_result.boxes.shape[0]:
                x1, y1, x2, y2 = seg_result.boxes[idx]
                h, w = seg_result.masks.shape[1], seg_result.masks.shape[2]
                if w > 0 and h > 0:
                    bbox = [
                        float((x1 + x2) / 2 / w),
                        float((y1 + y2) / 2 / h),
                        float((x2 - x1) / w),
                        float((y2 - y1) / h),
                    ]

            cls_idx = class_indices[idx] if idx < len(class_indices) else 0
            ann_class_name = class_names[cls_idx] if cls_idx < len(class_names) else class_name

            annotation = Annotation(
                frame_id=db_frame.id,
                job_id=job.id,
                class_name=ann_class_name,
                class_index=cls_idx,
                polygon=[],
                bbox=bbox,
                confidence=score,
            )
            session.add(annotation)

        _update_job(
            session, job,
            processed_frames=seg_result.frame_index + 1,
            progress=(seg_result.frame_index + 1) / len(frame_infos),
        )

    frame_paths = [fi.file_path for fi in frame_infos]
    to_classify.write_yolo_dataset(dataset_dir, frame_paths, crops_per_frame, class_names)
