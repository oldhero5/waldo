import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import review


def annotation(**changes):
    return SimpleNamespace(
        **(
            {
                "class_name": "camera",
                "class_index": 7,
                "status": "accepted",
                "bbox": [0.5, 0.5, 0.2, 0.2],
                "polygon": [0.4, 0.4, 0.6, 0.4, 0.6, 0.6],
            }
            | changes
        )
    )


def test_review_export_groups_source_videos_and_leaves_db_indices_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(b"image"))
    frames = {str(i): SimpleNamespace(video_id=f"video-{i // 2}", minio_key=f"frames/{i}.jpg") for i in range(4)}
    annotations = {fid: [annotation()] for fid in frames}
    review._write_review_export(tmp_path / "dataset", tmp_path / "sources", frames, annotations, ["camera"], "detect")
    manifest = json.loads((tmp_path / "dataset/manifest.json").read_text())
    assert manifest["task"] == "detect"
    assert manifest["split_strategy"] == "group"
    per_video = {}
    for sample in manifest["samples"]:
        per_video.setdefault(sample["group_id"], set()).add(sample["split"])
        assert (tmp_path / "dataset" / sample["label_path"]).read_text().startswith("0 ")
    assert all(len(splits) == 1 for splits in per_video.values())
    assert all(items[0].class_index == 7 for items in annotations.values())


def test_review_export_does_not_turn_rejected_only_frame_into_negative(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(b"image"))
    frames = {
        "a": SimpleNamespace(video_id="a", minio_key="a.jpg"),
        "b": SimpleNamespace(video_id="b", minio_key="b.jpg"),
    }
    anns = {"a": [annotation()], "b": [annotation(status="rejected")]}
    review._write_review_export(tmp_path / "dataset", tmp_path / "sources", frames, anns, ["camera"], "detect")
    samples = json.loads((tmp_path / "dataset/manifest.json").read_text())["samples"]
    assert len(samples) == 1 and samples[0]["group_id"] == "a"


def test_review_export_rejects_unrepresentable_annotation_instead_of_false_negative(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(b"image"))
    frames = {"a": SimpleNamespace(video_id="a", minio_key="a.jpg")}
    with pytest.raises(HTTPException) as exc:
        review._write_review_export(
            tmp_path / "dataset",
            tmp_path / "sources",
            frames,
            {"a": [annotation(), annotation(polygon=[])]},
            ["camera"],
            "segment",
        )
    assert exc.value.status_code == 400
