"""YOLO26 training orchestrator — wraps Ultralytics training API."""
import logging
import tempfile
from datetime import datetime
from pathlib import Path

from lib.config import settings
from lib.db import ModelRegistry, SessionLocal, TrainingRun
from lib.storage import download_file, upload_file
from trainer.dataset_builder import build_dataset_from_job, prepare_dataset_dir
from trainer.metrics_streamer import make_ultralytics_callback, publish_metrics
from trainer.notifiers import notify_training_complete

logger = logging.getLogger(__name__)

# Model variant → pretrained weights mapping
VARIANTS = {
    # YOLO26 Segmentation
    "yolo26n-seg": "yolo26n-seg.pt",
    "yolo26s-seg": "yolo26s-seg.pt",
    "yolo26m-seg": "yolo26m-seg.pt",
    "yolo26l-seg": "yolo26l-seg.pt",
    "yolo26x-seg": "yolo26x-seg.pt",
    # YOLO26 Detection
    "yolo26n": "yolo26n.pt",
    "yolo26s": "yolo26s.pt",
    "yolo26m": "yolo26m.pt",
    "yolo26l": "yolo26l.pt",
    "yolo26x": "yolo26x.pt",
    # YOLO11 Segmentation (legacy)
    "yolo11n-seg": "yolo11n-seg.pt",
    "yolo11s-seg": "yolo11s-seg.pt",
    "yolo11m-seg": "yolo11m-seg.pt",
    "yolo11l-seg": "yolo11l-seg.pt",
    "yolo11x-seg": "yolo11x-seg.pt",
    # YOLO11 Detection (legacy)
    "yolo11n": "yolo11n.pt",
    "yolo11s": "yolo11s.pt",
    "yolo11m": "yolo11m.pt",
    "yolo11l": "yolo11l.pt",
    "yolo11x": "yolo11x.pt",
    # Pose
    "yolo11n-pose": "yolo11n-pose.pt",
    "yolo11m-pose": "yolo11m-pose.pt",
    # OBB
    "yolo11n-obb": "yolo11n-obb.pt",
    "yolo11m-obb": "yolo11m-obb.pt",
    # Classification
    "yolo11n-cls": "yolo11n-cls.pt",
    "yolo11m-cls": "yolo11m-cls.pt",
    # RF-DETR (Real-time Transformer detection)
    "rf-detr-base": "rf-detr-base.pt",
    "rf-detr-large": "rf-detr-large.pt",
}

TASK_TO_DEFAULT_VARIANT = {
    "segment": "yolo26n-seg",
    "detect": "yolo26n",
    "detect_transformer": "rf-detr-base",
    "classify": "yolo11n-cls",
    "pose": "yolo11n-pose",
    "obb": "yolo11n-obb",
}

DEFAULT_HYPERPARAMS = {
    "epochs": 100,
    "imgsz": 640,
    "batch": 8,
    "patience": 2,   # Stop after 2 epochs of no improvement (user can override)
    "save_period": 10,
    "optimizer": "auto",
    "lr0": 0.01,
    "cos_lr": True,
}

# Augmentation presets — each adds to the base YOLO defaults
AUGMENTATION_PRESETS = {
    "minimal": {
        # Fastest training, least robust. Good for quick iteration.
        "mosaic": 0.0,
        "mixup": 0.0,
        "copy_paste": 0.0,
        "degrees": 0.0,
        "flipud": 0.0,
        "fliplr": 0.5,
        "hsv_h": 0.01,
        "hsv_s": 0.3,
        "hsv_v": 0.2,
        "erasing": 0.0,
        "scale": 0.3,
        "translate": 0.1,
        "shear": 0.0,
        "perspective": 0.0,
        "multi_scale": False,
    },
    "standard": {
        # Balanced: YOLO defaults + mixup + copy_paste + mild rotation.
        "mosaic": 1.0,
        "close_mosaic": 10,
        "mixup": 0.15,
        "copy_paste": 0.1,
        "degrees": 10.0,
        "flipud": 0.0,
        "fliplr": 0.5,
        "hsv_h": 0.015,
        "hsv_s": 0.7,
        "hsv_v": 0.4,
        "erasing": 0.4,
        "scale": 0.5,
        "translate": 0.1,
        "shear": 2.0,
        "perspective": 0.0001,
        "multi_scale": False,
    },
    "aggressive": {
        # Maximum robustness. Heavier augmentation for out-of-distribution generalization.
        "mosaic": 1.0,
        "close_mosaic": 15,
        "mixup": 0.3,
        "copy_paste": 0.3,
        "degrees": 25.0,
        "flipud": 0.3,
        "fliplr": 0.5,
        "hsv_h": 0.02,
        "hsv_s": 0.9,
        "hsv_v": 0.5,
        "erasing": 0.5,
        "scale": 0.9,
        "translate": 0.2,
        "shear": 5.0,
        "perspective": 0.001,
        "multi_scale": True,
    },
}


def _update_run(session, run: TrainingRun, **kwargs) -> None:
    for k, v in kwargs.items():
        setattr(run, k, v)
    session.commit()


def run_training(celery_task, run_id: str) -> dict:
    """Execute a YOLO training run."""
    from ultralytics import YOLO

    session = SessionLocal()
    try:
        run = session.query(TrainingRun).filter_by(id=run_id).one()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Phase 1: Prepare dataset
            _update_run(session, run, status="preparing", started_at=datetime.utcnow())
            celery_task.update_state(state="PREPARING")

            dataset_key = run.dataset_minio_key
            if not dataset_key and run.job_id:
                dataset_key = build_dataset_from_job(str(run.job_id))
                _update_run(session, run, dataset_minio_key=dataset_key)

            dataset_dir = prepare_dataset_dir(dataset_key, tmpdir)
            data_yaml = str(dataset_dir / "data.yaml")

            # Validate dataset is non-empty
            train_imgs = list((dataset_dir / "images" / "train").glob("*"))
            train_labels = list((dataset_dir / "labels" / "train").glob("*.txt"))
            non_empty_labels = [l for l in train_labels if l.stat().st_size > 0]
            if not train_imgs:
                raise ValueError(f"Dataset has no training images (expected in {dataset_dir / 'images' / 'train'})")
            if not non_empty_labels:
                raise ValueError(
                    f"Dataset has {len(train_imgs)} images but no label files with annotations. "
                    f"This usually means all annotations were filtered out during conversion. "
                    f"Check that your labeled objects are large enough (min_area=100px²)."
                )

            # Phase 2: Load model (pretrained or from checkpoint)
            variant = run.model_variant
            all_hp = {**DEFAULT_HYPERPARAMS, **(run.hyperparameters or {})}
            resume_from = all_hp.pop("resume_from", None)

            if resume_from:
                # Fine-tune from an existing model's weights
                checkpoint_path = tmpdir / "checkpoint.pt"
                entry = session.query(ModelRegistry).filter_by(id=resume_from).first()
                if entry and entry.weights_minio_key:
                    download_file(entry.weights_minio_key, checkpoint_path)
                    logger.info("Resuming from checkpoint: %s (%s)", entry.name, resume_from)
                    model = YOLO(str(checkpoint_path))
                else:
                    logger.warning("resume_from model %s not found, using pretrained", resume_from)
                    model = YOLO(VARIANTS.get(variant, f"{variant}.pt"))
            else:
                weights = VARIANTS.get(variant, f"{variant}.pt")
                model = YOLO(weights)

            # Phase 3: Setup callbacks for real-time metrics
            callbacks = make_ultralytics_callback(str(run.id), session, run)
            model.add_callback("on_train_batch_end", callbacks["on_train_batch_end"])
            model.add_callback("on_train_epoch_end", callbacks["on_train_epoch_end"])
            model.add_callback("on_train_end", callbacks["on_train_end"])
            if "on_val_end" in callbacks:
                model.add_callback("on_val_end", callbacks["on_val_end"])

            # Phase 4: Train
            _update_run(session, run, status="training")
            celery_task.update_state(state="TRAINING")
            publish_metrics(str(run.id), {
                "run_id": str(run.id), "status": "training", "epoch": 0,
                "total_epochs": run.total_epochs, "metrics": {},
            })

            hp = all_hp  # Already constructed in Phase 2

            # Resolve augmentation preset
            aug_preset_name = hp.pop("augmentation", "standard")
            aug_params = AUGMENTATION_PRESETS.get(aug_preset_name, AUGMENTATION_PRESETS["standard"]).copy()
            # Allow individual augmentation overrides from hyperparameters
            for aug_key in list(AUGMENTATION_PRESETS["standard"].keys()):
                if aug_key in hp:
                    aug_params[aug_key] = hp.pop(aug_key)

            device = settings.device
            if device == "mps":
                device = "mps"
            elif device == "cuda":
                device = 0

            train_dir = tmpdir / "runs"
            results = model.train(
                data=data_yaml,
                epochs=hp["epochs"],
                imgsz=hp["imgsz"],
                batch=hp["batch"],
                patience=hp["patience"],
                save_period=hp["save_period"],
                optimizer=hp["optimizer"],
                lr0=hp["lr0"],
                cos_lr=hp["cos_lr"],
                device=device,
                project=str(train_dir),
                name="train",
                verbose=True,
                plots=True,
                # Augmentation
                **aug_params,
            )

            # Phase 5: Collect results
            _update_run(session, run, status="validating")
            celery_task.update_state(state="VALIDATING")

            best_weights = train_dir / "train" / "weights" / "best.pt"
            last_weights = train_dir / "train" / "weights" / "last.pt"

            final_metrics = {}
            if hasattr(results, "results_dict"):
                for k, v in results.results_dict.items():
                    final_metrics[k] = float(v) if hasattr(v, "__float__") else v

            # Upload best weights to MinIO
            weights_key = f"models/{run.id}/best.pt"
            if best_weights.exists():
                upload_file(weights_key, best_weights)
            elif last_weights.exists():
                upload_file(weights_key, last_weights)
                weights_key = f"models/{run.id}/last.pt"

            # Read class names from data.yaml
            import yaml
            class_names_list = None
            data_yaml_path = Path(data_yaml)
            if data_yaml_path.exists():
                with open(data_yaml_path) as f:
                    data_cfg = yaml.safe_load(f)
                names = data_cfg.get("names")
                if isinstance(names, dict):
                    class_names_list = [names[k] for k in sorted(names.keys())]
                elif isinstance(names, list):
                    class_names_list = names

            # Register model
            model_entry = ModelRegistry(
                project_id=run.project_id,
                training_run_id=run.id,
                name=run.name,
                task_type=run.task_type,
                model_variant=run.model_variant,
                weights_minio_key=weights_key,
                metrics=final_metrics,
                class_names=class_names_list,
            )
            session.add(model_entry)

            _update_run(
                session, run,
                status="completed",
                best_metrics=final_metrics,
                best_weights_minio_key=weights_key,
                completed_at=datetime.utcnow(),
            )

            # Phase 6: Notify
            notify_training_complete(run.name, final_metrics, str(run.id))

            return {"status": "completed", "weights_key": weights_key, "metrics": final_metrics}

    except Exception as e:
        session.rollback()
        try:
            run = session.query(TrainingRun).filter_by(id=run_id).one()
            _update_run(session, run, status="failed", error_message=str(e))
            publish_metrics(str(run.id), {
                "run_id": str(run.id), "status": "failed", "error": str(e),
            })
        except Exception:
            pass
        raise
    finally:
        session.close()
