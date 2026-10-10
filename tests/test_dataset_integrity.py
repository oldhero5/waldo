"""Source ancestry, label geometry, and export integrity regressions."""

import json
import shutil
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
import yaml

from labeler.converters import to_classify, to_detect, to_obb, to_pose, to_segment
from trainer import dataset_builder

pytestmark = pytest.mark.no_auth_bypass


def _dataset(root, task="detect"):
    for split in ("train", "val"):
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)
    (root / "data.yaml").write_text(yaml.safe_dump({"path": ".", "names": ["camera", "pole"], "waldo_task": task}))
    return root


def _image(path, value=80, size=200):
    cv2.imwrite(str(path), np.full((size, size, 3), value, dtype=np.uint8))
    return path


def test_unanchored_demo_feedback_does_not_modify_dataset(monkeypatch, tmp_path, caplog):
    source = _dataset(tmp_path / "source", task="obb")
    _image(source / "images/train/frame.png")
    original = "0 0.4 0.4 0.6 0.4 0.6 0.6 0.4 0.6\n"
    (source / "labels/train/frame.txt").write_text(original)
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in source.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(source))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(dataset_builder, "download_file", lambda key, path: shutil.copyfile(archive, path))

    import lib.db

    session = MagicMock()
    session.query.return_value.filter_by.return_value.all.return_value = [
        SimpleNamespace(class_name="camera", bbox=[80, 80, 120, 120])
    ]
    monkeypatch.setattr(lib.db, "SessionLocal", lambda: session)
    result = dataset_builder.prepare_dataset_dir("datasets/input.zip", work)

    assert (result / "labels/train/frame.txt").read_text() == original
    assert "provenance" in caplog.text.lower()


@pytest.mark.parametrize("writer", [to_detect, to_segment, to_obb, to_pose])
def test_same_basename_exports_keep_pixels_and_corresponding_labels(tmp_path, writer):
    paths = []
    for video, value in (("drive-a", 40), ("drive-b", 210)):
        directory = tmp_path / video
        directory.mkdir()
        paths.append(_image(directory / "frame_000001.png", value))
    labels = [["0 0.1 0.2 0.3 0.4"], ["1 0.5 0.6 0.7 0.8"]]

    output = writer.write_yolo_dataset(tmp_path / "export", paths, labels, ["camera", "pole"], val_split=0)

    images = list((output / "images/train").glob("*.png"))
    assert len(images) == 2
    manifest = json.loads((output / "manifest.json").read_text())
    for entry in manifest["samples"]:
        index = entry["source_index"]
        assert cv2.imread(str(output / entry["image_path"]))[0, 0, 0] == [40, 210][index]
        assert (output / entry["label_path"]).read_text().strip() == labels[index][0]
        assert entry["source_path"] == str(paths[index])
    assert manifest["split_strategy"] == "frame"


@pytest.mark.parametrize("writer", [to_detect, to_segment, to_obb, to_pose, to_classify])
def test_writers_reject_frame_annotation_length_mismatch(tmp_path, writer):
    with pytest.raises(ValueError, match="length"):
        writer.write_yolo_dataset(tmp_path / "export", [tmp_path / "frame.png"], [], ["camera"])
    assert not (tmp_path / "export").exists()


def test_source_groups_have_deterministic_disjoint_splits(tmp_path):
    paths, groups = [], ["drive-a", "drive-a", "drive-b", "drive-b", "drive-c", "drive-c"]
    for index in range(6):
        paths.append(_image(tmp_path / f"frame{index}.png", index))
    assignments = []
    for order, name in ((list(range(6)), "first"), ([5, 2, 1, 4, 0, 3], "reordered")):
        output = to_detect.write_yolo_dataset(
            tmp_path / name,
            [paths[i] for i in order],
            [["0 0.5 0.5 0.2 0.2"] for _ in order],
            ["camera"],
            val_split=0.34,
            group_ids=[groups[i] for i in order],
        )
        manifest = json.loads((output / "manifest.json").read_text())
        group_splits = {}
        for row in manifest["samples"]:
            group_splits.setdefault(row["group_id"], set()).add(row["split"])
        assert all(len(splits) == 1 for splits in group_splits.values())
        assert set.union(*group_splits.values()) == {"train", "val"}
        assert manifest["split_strategy"] == "group"
        assignments.append(group_splits)
    assert assignments[0] == assignments[1]


def test_classification_crops_inherit_frame_split(tmp_path):
    crop = np.full((12, 12, 3), 90, dtype=np.uint8)
    output = to_classify.write_yolo_dataset(
        tmp_path / "export",
        [tmp_path / "frame.png"],
        [[(crop, "camera"), (crop, "camera")]],
        ["camera"],
    )
    assert len(list((output / "train/camera").glob("*.jpg"))) == 2
    assert list((output / "val/camera").glob("*.jpg")) == []
    manifest = json.loads((output / "manifest.json").read_text())
    assert [row["source_index"] for row in manifest["samples"]] == [0, 0]
    assert [row["crop_index"] for row in manifest["samples"]] == [0, 1]


def test_export_rejects_existing_destination_without_overwriting_pixels(tmp_path):
    frame = _image(tmp_path / "frame.png", 60)
    output = to_detect.write_yolo_dataset(tmp_path / "export", [frame], [["0 0.5 0.5 0.1 0.1"]], ["camera"])
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    _image(frame, 200)

    with pytest.raises(FileExistsError, match="destination"):
        to_detect.write_yolo_dataset(output, [frame], [["0 0.5 0.5 0.2 0.2"]], ["camera"])

    assert {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()} == before


def test_detection_crop_clips_large_neighbor_instead_of_erasing_it(monkeypatch, tmp_path):
    root = _dataset(tmp_path / "dataset")
    _image(root / "images/train/frame.png")
    (root / "labels/train/frame.txt").write_text("0 0.5 0.5 0.02 0.02\n1 0.5 0.5 0.8 0.8\n")
    monkeypatch.setattr(dataset_builder, "CROP_SCALES", [0.22])

    dataset_builder._augment_small_objects(root)

    rows = (root / "labels/train/crop_frame_0_0_0.txt").read_text().splitlines()
    assert rows[1] == "1 0.500000 0.500000 1.000000 1.000000"


def test_detection_crop_uses_actual_pixel_bounds_and_all_visible_labels(monkeypatch, tmp_path):
    root = _dataset(tmp_path / "dataset")
    _image(root / "images/train/frame.png", size=201)
    image = np.zeros((201, 201, 3), dtype=np.uint8)
    image[:, :, 0] = np.arange(201, dtype=np.uint8)[:, None]
    cv2.imwrite(str(root / "images/train/frame.png"), image)
    # Both tiny boxes fit in the centered 22% crop; their right edge is x=.54.
    (root / "labels/train/frame.txt").write_text("0 0.50 0.50 0.02 0.02\n1 0.53 0.50 0.02 0.02\n")
    _image(root / "images/val/heldout.png", 200)
    (root / "labels/val/heldout.txt").write_text("0 0.5 0.5 0.02 0.02\n")
    original_val = {p.relative_to(root): p.read_bytes() for p in (root / "images/val").iterdir()}
    original_val.update({p.relative_to(root): p.read_bytes() for p in (root / "labels/val").iterdir()})
    monkeypatch.setattr(dataset_builder, "CROP_SCALES", [0.22])

    dataset_builder._augment_small_objects(root)

    assert {
        p.relative_to(root): p.read_bytes()
        for folder in ("images/val", "labels/val")
        for p in (root / folder).iterdir()
    } == original_val
    crop_labels = list((root / "labels/train").glob("crop_*.txt"))
    assert crop_labels
    centered = next(path for path in crop_labels if "0.511364 0.511364" in path.read_text())
    rows = [line.split() for line in centered.read_text().splitlines()]
    assert [row[0] for row in rows] == ["0", "1"]
    # Crop [78:122] contains source center 100.5. Geometry uses those integer bounds.
    assert [float(value) for value in rows[0][1:]] == pytest.approx(
        [22.5 / 44, 22.5 / 44, 4.02 / 44, 4.02 / 44], abs=1e-6
    )
    manifest = json.loads((root / "manifest.json").read_text())
    sample = next(row for row in manifest["samples"] if row["label_path"] == str(centered.relative_to(root)))
    assert sample["parent_image_path"] == "images/train/frame.png"
    assert sample["crop_bounds_px"] == [78, 78, 122, 122]
    crop = cv2.imread(str(root / sample["image_path"]))
    assert crop.shape == (640, 640, 3)
    assert float(crop[0, :, 0].mean()) == pytest.approx(78, abs=2)
    assert float(crop[-1, :, 0].mean()) == pytest.approx(121, abs=2)


def test_segment_crop_retains_neighbor_labels_without_cutting_them(monkeypatch, tmp_path):
    root = _dataset(tmp_path / "dataset", "segment")
    _image(root / "images/train/frame.png")
    (root / "labels/train/frame.txt").write_text(
        "0 0.49 0.49 0.51 0.49 0.51 0.51 0.49 0.51\n1 0.52 0.49 0.54 0.49 0.54 0.51 0.52 0.51\n"
    )
    monkeypatch.setattr(dataset_builder, "CROP_SCALES", [0.22])

    dataset_builder._augment_small_objects(root)

    label = root / "labels/train/crop_frame_0_0_0.txt"
    rows = [line.split() for line in label.read_text().splitlines()]
    assert [row[0] for row in rows] == ["0", "1"]
    assert [float(value) for value in rows[0][1:]] == pytest.approx(
        [20 / 44, 20 / 44, 24 / 44, 20 / 44, 24 / 44, 24 / 44, 20 / 44, 24 / 44],
        abs=1e-6,
    )


def test_segment_crop_skips_partially_visible_neighbor_instead_of_omitting_label(monkeypatch, tmp_path):
    root = _dataset(tmp_path / "dataset", "segment")
    _image(root / "images/train/frame.png")
    (root / "labels/train/frame.txt").write_text(
        "0 0.49 0.49 0.51 0.49 0.51 0.51 0.49 0.51\n1 0.0 0.0 1.0 0.0 1.0 1.0 0.0 1.0\n"
    )
    monkeypatch.setattr(dataset_builder, "CROP_SCALES", [0.22])

    dataset_builder._augment_small_objects(root)

    assert list((root / "images/train").glob("crop_*.jpg")) == []
    assert list((root / "labels/train").glob("crop_*.txt")) == []


def test_missing_labels_are_not_fabricated_as_reviewed_background(tmp_path, caplog):
    root = _dataset(tmp_path / "dataset")
    _image(root / "images/train/unreviewed.png")
    _image(root / "images/val/unknown.png")

    dataset_builder._ensure_background_images(root)

    assert list((root / "labels/train").iterdir()) == []
    assert list((root / "labels/val").iterdir()) == []
    assert "missing" in caplog.text.lower()


@pytest.mark.parametrize("task", ["obb", "pose", "unknown"])
def test_unsupported_crop_tasks_leave_labels_and_pixels_intact(tmp_path, task, caplog):
    root = _dataset(tmp_path / "dataset", task)
    _image(root / "images/train/frame.png")
    label = root / "labels/train/frame.txt"
    label.write_text("0 0.49 0.49 0.51 0.49 0.51 0.51 0.49 0.51\n")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    dataset_builder._augment_small_objects(root)

    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert "skip" in caplog.text.lower()
