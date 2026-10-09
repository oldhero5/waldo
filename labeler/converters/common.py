"""Shared dataset-writing utilities for all YOLO converters."""

import hashlib
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_data_yaml(class_names: list[str], task: str = "segment") -> str:
    import yaml

    data = {
        "path": ".",
        "train": "images/train",
        "val": "images/val",
        "nc": len(class_names),
        "names": {i: name for i, name in enumerate(class_names)},
        "waldo_task": task,
    }
    if task == "pose":
        data["kpt_shape"] = [1, 3]
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def split_indices(count: int, val_split: float = 0.1, group_ids: list[str] | None = None) -> set[int]:
    """Deterministically split whole source groups, or frames when groups are unknown."""
    if not 0 <= val_split <= 1:
        raise ValueError("val_split must be between 0 and 1")
    if group_ids is not None and len(group_ids) != count:
        raise ValueError("group_ids length must match frame_paths length")
    groups = group_ids if group_ids is not None else [str(i) for i in range(count)]
    if any(not isinstance(group, str) or not group for group in groups):
        raise ValueError("group_ids must contain non-empty source identifiers")
    unique = sorted(set(groups), key=lambda group: hashlib.sha256(group.encode()).hexdigest())
    if val_split == 0:
        val_count = 0
    elif val_split == 1:
        val_count = len(unique)
    else:
        val_count = min(len(unique) - 1, max(1, int(len(unique) * val_split))) if len(unique) > 1 else 0
    selected = set(unique[:val_count])
    return {index for index, group in enumerate(groups) if group in selected}


def export_stem(frame_path: Path, index: int) -> str:
    """Unique within this export, including identical basenames from different videos."""
    identity = hashlib.sha256(str(frame_path.resolve()).encode()).hexdigest()[:12]
    return f"frame_{index:06d}_{identity}"


def write_manifest(output_dir: Path, samples: list[dict], task: str, grouped: bool) -> None:
    strategy = "group" if grouped else "frame"
    if not grouped:
        logger.warning("Dataset uses a frame split without source groups; validation is not temporally independent")
    manifest = {"version": 1, "task": task, "split_strategy": strategy, "samples": samples}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def write_yolo_label_dataset(
    output_dir: str | Path,
    frame_paths: list[Path],
    annotation_lines: list[list[str]],
    class_names: list[str],
    val_split: float = 0.1,
    task: str = "segment",
    *,
    group_ids: list[str] | None = None,
) -> Path:
    """Write a standard YOLO dataset with images/ and labels/ directories."""
    output_dir = Path(output_dir)
    if len(frame_paths) != len(annotation_lines):
        raise ValueError("frame_paths and annotation_lines length must match")
    val_indices = split_indices(len(frame_paths), val_split, group_ids)
    destinations = []
    for i, frame_path in enumerate(frame_paths):
        split = "val" if i in val_indices else "train"
        stem = export_stem(frame_path, i)
        image = output_dir / "images" / split / (stem + frame_path.suffix)
        label = output_dir / "labels" / split / (stem + ".txt")
        if image.exists() or label.exists():
            raise FileExistsError(f"Export destination already exists: {image}")
        destinations.append((split, image, label))

    for split in ("train", "val"):
        (output_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    samples = []
    for i, (frame_path, ann_lines) in enumerate(zip(frame_paths, annotation_lines)):
        split, dst_img, dst_label = destinations[i]
        shutil.copy2(frame_path, dst_img)

        dst_label.write_text("\n".join(ann_lines) + "\n" if ann_lines else "")
        samples.append(
            {
                "source_index": i,
                "source_path": str(frame_path),
                "group_id": group_ids[i] if group_ids is not None else None,
                "split": split,
                "image_path": str(dst_img.relative_to(output_dir)),
                "label_path": str(dst_label.relative_to(output_dir)),
            }
        )

    yaml_path = output_dir / "data.yaml"
    yaml_path.write_text(generate_data_yaml(class_names, task))
    write_manifest(output_dir, samples, task, group_ids is not None)

    return output_dir
