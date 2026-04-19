"""Shared dataset-writing utilities for all YOLO converters."""

import random
import shutil
from pathlib import Path


def generate_data_yaml(class_names: list[str], task: str = "segment") -> str:
    import yaml

    data = {
        "path": ".",
        "train": "images/train",
        "val": "images/val",
        "nc": len(class_names),
        "names": {i: name for i, name in enumerate(class_names)},
    }
    if task == "pose":
        data["kpt_shape"] = [1, 3]
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def split_indices(count: int, val_split: float = 0.1) -> set[int]:
    indices = list(range(count))
    random.shuffle(indices)
    val_count = max(1, int(count * val_split)) if count > 1 else 0
    return set(indices[:val_count])


def write_yolo_label_dataset(
    output_dir: str | Path,
    frame_paths: list[Path],
    annotation_lines: list[list[str]],
    class_names: list[str],
    val_split: float = 0.1,
    task: str = "segment",
) -> Path:
    """Write a standard YOLO dataset with images/ and labels/ directories."""
    output_dir = Path(output_dir)

    for split in ("train", "val"):
        (output_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    val_indices = split_indices(len(frame_paths), val_split)

    for i, (frame_path, ann_lines) in enumerate(zip(frame_paths, annotation_lines)):
        split = "val" if i in val_indices else "train"

        dst_img = output_dir / "images" / split / frame_path.name
        shutil.copy2(frame_path, dst_img)

        label_name = frame_path.stem + ".txt"
        dst_label = output_dir / "labels" / split / label_name
        dst_label.write_text("\n".join(ann_lines) + "\n" if ann_lines else "")

    yaml_path = output_dir / "data.yaml"
    yaml_path.write_text(generate_data_yaml(class_names, task))

    return output_dir
