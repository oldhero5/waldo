"""Checkpoint orchestration regressions without training or external services."""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from trainer import train_manager

pytestmark = pytest.mark.no_auth_bypass


@pytest.fixture
def training(monkeypatch, tmp_path):
    run = SimpleNamespace(
        id="checkpoint-regression",
        name="camera",
        project_id=uuid.uuid4(),
        task_type="detect",
        dataset_minio_key="datasets/test.zip",
        job_id=None,
        model_variant="yolo26n",
        hyperparameters={},
        total_epochs=1,
        epoch_current=0,
    )
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one.return_value = run
    session.query.return_value.filter_by.return_value.first.return_value = SimpleNamespace(
        name="previous", weights_minio_key="models/previous/best.pt", project_id=run.project_id
    )
    monkeypatch.setattr(train_manager, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        train_manager,
        "Path",
        lambda value: (
            tmp_path / "runs" / value.removeprefix("/tmp/waldo-runs/")
            if str(value).startswith("/tmp/waldo-runs/")
            else Path(value)
        ),
    )
    monkeypatch.setattr(train_manager, "publish_metrics", lambda *a: None)
    monkeypatch.setattr(train_manager, "notify_training_complete", lambda *a: None)
    monkeypatch.setattr(
        train_manager,
        "make_ultralytics_callback",
        lambda *a: {event: lambda *a: None for event in ("on_train_batch_end", "on_train_epoch_end", "on_train_end")},
    )

    def dataset(key, directory):
        root = directory / "dataset"
        (root / "images/train").mkdir(parents=True)
        (root / "labels/train").mkdir(parents=True)
        (root / "images/train/camera.jpg").write_bytes(b"image")
        (root / "labels/train/camera.txt").write_text("0 0.5 0.5 0.2 0.2\n")
        (root / "data.yaml").write_text("names: [camera]\n")
        return root

    monkeypatch.setattr(train_manager, "prepare_dataset_dir", dataset)
    uploads, loads, train_calls = {}, [], []
    checkpoint = {"filename": "best.pt"}

    class FakeYOLO:
        def __init__(self, weights):
            loads.append(weights)

        def add_callback(self, event, callback):
            pass

        def train(self, **kwargs):
            train_calls.append(kwargs)
            output = Path(kwargs["project"]) / "train/weights"
            output.mkdir(parents=True)
            (output / checkpoint["filename"]).write_bytes(b"trained-checkpoint")
            return SimpleNamespace(results_dict={"metrics/mAP50(B)": 0.8})

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))
    monkeypatch.setattr(train_manager, "upload_file", lambda key, path: uploads.update({key: path.read_bytes()}))
    monkeypatch.setattr(train_manager, "download_file", lambda key, path: path.write_bytes(b"prior-checkpoint"))
    return SimpleNamespace(
        run=run, session=session, uploads=uploads, loads=loads, train_calls=train_calls, checkpoint=checkpoint
    )


@pytest.mark.parametrize("checkpoint_found", [True, False])
def test_explicit_resume_from_reaches_training_without_auto_resume(monkeypatch, training, checkpoint_found):
    training.run.hyperparameters = {"resume_from": str(uuid.uuid4())}
    if not checkpoint_found:
        training.session.query.return_value.filter_by.return_value.first.return_value = None

    if checkpoint_found:
        result = train_manager.run_training(MagicMock(), training.run.id)
        assert result["status"] == "completed"
        assert "resume" not in training.train_calls[0]
        assert Path(training.loads[0]).name == "checkpoint.pt"
    else:
        with pytest.raises(ValueError, match="checkpoint"):
            train_manager.run_training(MagicMock(), training.run.id)
        assert training.loads == []
        assert training.train_calls == []


def test_fallback_checkpoint_registered_key_resolves_to_uploaded_weights(training):
    training.checkpoint["filename"] = "last.pt"

    result = train_manager.run_training(MagicMock(), training.run.id)

    key = "models/checkpoint-regression/last.pt"
    assert result["weights_key"] == key
    assert training.uploads == {key: b"trained-checkpoint"}
    assert training.run.best_weights_minio_key == key
    model = training.session.add.call_args.args[0]
    assert model.weights_minio_key == key


def test_explicit_resume_rejects_checkpoint_from_other_project(training):
    training.run.hyperparameters = {"resume_from": str(uuid.uuid4())}
    training.session.query.return_value.filter_by.return_value.first.return_value.project_id = uuid.uuid4()
    with pytest.raises(ValueError, match="project"):
        train_manager.run_training(MagicMock(), training.run.id)
    assert training.loads == []
    assert training.train_calls == []


def test_explicit_resume_rejects_invalid_registry_id_before_lookup(training):
    training.run.hyperparameters = {"resume_from": "not-a-model-uuid"}
    with pytest.raises(ValueError, match="resume_from"):
        train_manager.run_training(MagicMock(), training.run.id)
    assert training.loads == []
    assert training.train_calls == []


def test_training_rejects_single_source_group_instead_of_leaking_validation(monkeypatch, training):
    original = train_manager.prepare_dataset_dir

    def grouped_dataset(key, directory):
        dataset = original(key, directory)
        (dataset / "manifest.json").write_text(
            '{"split_strategy":"group","samples":[{"group_id":"only-video","split":"train"}]}'
        )
        return dataset

    monkeypatch.setattr(train_manager, "prepare_dataset_dir", grouped_dataset)
    with pytest.raises(ValueError, match="two independent source"):
        train_manager.run_training(MagicMock(), training.run.id)
    assert training.loads == []
    assert training.train_calls == []
