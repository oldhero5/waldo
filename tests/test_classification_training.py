"""Classification directory handoff with real exports/DB and a fake trainer.

Crops have class-name directory labels and source-video groups; adjacent crops
share their source split. No pose, object position, or timing is inferred here.
"""

import shutil
import sys
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
import yaml
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from labeler.converters.common import write_yolo_label_dataset
from labeler.converters.to_classify import write_yolo_dataset
from lib.db import Base, ModelRegistry, Project, TrainingRun
from trainer import dataset_builder, train_manager


@pytest.fixture
def training(monkeypatch, tmp_path):
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    sessions = sessionmaker(bind=database)
    monkeypatch.setattr(train_manager, "SessionLocal", sessions)
    monkeypatch.setattr(
        train_manager,
        "Path",
        lambda value: (
            tmp_path / "runs" / str(value).removeprefix("/tmp/waldo-runs/")
            if str(value).startswith("/tmp/waldo-runs/")
            else Path(value)
        ),
    )
    monkeypatch.setattr(train_manager, "publish_metrics", lambda *a: None)
    monkeypatch.setattr(train_manager, "notify_training_complete", lambda *a: None)
    monkeypatch.setattr(
        train_manager,
        "make_ultralytics_callback",
        lambda *a: {name: lambda *a: None for name in ("on_train_batch_end", "on_train_epoch_end", "on_train_end")},
    )
    calls, loads, uploads = [], [], {}

    class FakeYOLO:
        def __init__(self, weights):
            loads.append(weights)

        def add_callback(self, *a):
            pass

        def train(self, **kwargs):
            data = Path(kwargs["data"])
            assert data.exists()
            calls.append(kwargs)
            output = Path(kwargs["project"]) / "train/weights"
            output.mkdir(parents=True)
            (output / "best.pt").write_bytes(b"synthetic-trained-checkpoint")
            return SimpleNamespace(results_dict={"metrics/accuracy_top1": 0.75})

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))
    monkeypatch.setattr(train_manager, "upload_file", lambda key, path: uploads.update({key: path.read_bytes()}))
    monkeypatch.setattr(train_manager, "download_file", lambda key, path: path.write_bytes(b"synthetic-checkpoint"))

    def make(task="classify", groups=None):
        root = tmp_path / uuid.uuid4().hex
        root.mkdir()
        sources = [root / f"source-{index}.png" for index in range(2)]
        crop = np.full((16, 16, 3), 127, dtype=np.uint8)
        for source in sources:
            Image.fromarray(crop).save(source)
        export = root / "export"
        groups = groups if groups is not None else ["video-a", "video-b"]
        if task == "classify":
            write_yolo_dataset(
                export, sources, [[(crop, "pole"), (crop, "camera")]] * 2, ["pole", "camera"], group_ids=groups
            )
        else:
            line = "0 0.4 0.4 0.6 0.4 0.6 0.6 0.4 0.6" if task == "segment" else "0 0.5 0.5 0.2 0.2"
            write_yolo_label_dataset(export, sources, [[line]] * 2, ["pole", "camera"], task=task, group_ids=groups)
        archive = root / "dataset.zip"

        def pack():
            with zipfile.ZipFile(archive, "w") as output:
                for path in export.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(export))

        pack()
        monkeypatch.setattr(dataset_builder, "download_file", lambda key, path: shutil.copyfile(archive, path))
        with sessions() as session:
            project = Project(name="classification source grouping")
            session.add(project)
            session.flush()
            run = TrainingRun(
                project_id=project.id,
                name="synthetic training",
                task_type=task,
                model_variant="yolo11n-cls" if task == "classify" else "yolo26n",
                dataset_minio_key="synthetic/dataset.zip",
                hyperparameters={"epochs": 3},
                total_epochs=3,
            )
            session.add(run)
            session.commit()
            run_id, project_id = run.id, project.id
        return SimpleNamespace(
            run_id=run_id,
            project_id=project_id,
            export=export,
            pack=pack,
            sessions=sessions,
            calls=calls,
            loads=loads,
            uploads=uploads,
        )

    yield make
    database.dispose()


def test_classification_trains_from_directory_and_persists_actual_class_order(training):
    run = training()
    metadata = yaml.safe_load((run.export / "data.yaml").read_text())
    assert list(metadata["names"].values()) == ["pole", "camera"]
    result = train_manager.run_training(MagicMock(), run.run_id)
    assert result["status"] == "completed"
    call = run.calls[0]
    assert Path(call["data"]).name == "dataset"
    assert call["epochs"] == 3
    assert call["imgsz"] == 640 and call["batch"] == 8 and call["optimizer"] == "auto"
    assert "resume" not in call
    with run.sessions() as session:
        saved = session.get(TrainingRun, run.run_id)
        model = session.query(ModelRegistry).filter_by(training_run_id=run.run_id).one()
        assert saved.status == "completed" and saved.best_metrics == {"metrics/accuracy_top1": 0.75}
        assert model.task_type == "classify" and model.class_names == ["camera", "pole"]
        assert model.weights_minio_key == saved.best_weights_minio_key == result["weights_key"]
        assert run.uploads[model.weights_minio_key] == b"synthetic-trained-checkpoint"


def test_classification_yaml_describes_actual_split_directories(training):
    run = training()
    metadata = yaml.safe_load((run.export / "data.yaml").read_text())
    assert (metadata["train"], metadata["val"]) == ("train", "val")


@pytest.mark.parametrize("task", ["detect", "segment"])
def test_other_tasks_keep_yaml_and_numeric_label_class_order(training, task):
    run = training(task=task)
    assert train_manager.run_training(MagicMock(), run.run_id)["status"] == "completed"
    assert Path(run.calls[0]["data"]).name == "data.yaml"
    with run.sessions() as session:
        assert session.query(ModelRegistry).one().class_names == ["pole", "camera"]


def test_classification_keeps_explicit_resume_semantics(training):
    run = training()
    with run.sessions() as session:
        checkpoint = ModelRegistry(
            project_id=run.project_id,
            training_run_id=run.run_id,
            task_type="classify",
            model_variant="yolo11n-cls",
            name="prior",
            weights_minio_key="prior/best.pt",
        )
        session.add(checkpoint)
        session.flush()
        session.get(TrainingRun, run.run_id).hyperparameters = {"epochs": 3, "resume_from": str(checkpoint.id)}
        session.commit()
    assert train_manager.run_training(MagicMock(), run.run_id)["status"] == "completed"
    assert Path(run.loads[0]).name == "checkpoint.pt"
    assert "resume" not in run.calls[0]


def test_classification_requires_two_source_groups(training):
    run = training(groups=["same-video", "same-video"])
    with pytest.raises(ValueError, match="two independent source groups"):
        train_manager.run_training(MagicMock(), run.run_id)
    assert not run.loads and not run.calls


@pytest.mark.parametrize("case", ["missing_class", "empty_validation", "zero_byte_images"])
def test_classification_rejects_unusable_splits_before_model_loading(training, case):
    run = training()
    if case == "missing_class":
        shutil.rmtree(run.export / "val/camera")
        expected = "class directories.*add sources.*export"
    elif case == "empty_validation":
        shutil.rmtree(run.export / "val")
        expected = "validation images.*add sources.*export"
    else:
        for path in (run.export / "val").rglob("*.jpg"):
            path.write_bytes(b"")
        expected = "validation images.*add sources.*export"
    run.pack()
    with pytest.raises(ValueError, match=expected):
        train_manager.run_training(MagicMock(), run.run_id)
    assert not run.loads and not run.calls
    with run.sessions() as session:
        assert session.get(TrainingRun, run.run_id).status == "failed"
