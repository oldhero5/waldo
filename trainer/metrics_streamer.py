"""Stream training metrics to Redis for real-time WebSocket consumption."""
import json

import redis

from lib.config import settings

CHANNEL_PREFIX = "waldo:training:metrics:"


def get_redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url)


def publish_metrics(run_id: str, metrics: dict) -> None:
    """Publish training metrics to a Redis channel."""
    client = get_redis_client()
    channel = f"{CHANNEL_PREFIX}{run_id}"
    client.publish(channel, json.dumps(metrics))
    # Also store latest metrics for clients that connect late
    client.set(f"waldo:training:latest:{run_id}", json.dumps(metrics), ex=3600)


def get_latest_metrics(run_id: str) -> dict | None:
    """Get the most recent metrics for a training run."""
    client = get_redis_client()
    data = client.get(f"waldo:training:latest:{run_id}")
    if data:
        return json.loads(data)
    return None


def _has_nan(metrics: dict) -> bool:
    """Check if any metric value is NaN."""
    import math

    for v in metrics.values():
        try:
            if math.isnan(float(v)):
                return True
        except (TypeError, ValueError):
            pass
    return False


def should_stop_training(run_id: str) -> bool:
    """Check if the user has requested early stop via Redis flag."""
    client = get_redis_client()
    return client.get(f"waldo:training:stop:{run_id}") is not None


def request_stop(run_id: str) -> None:
    """Set a flag in Redis to request early stopping."""
    client = get_redis_client()
    client.set(f"waldo:training:stop:{run_id}", "1", ex=3600)


def make_ultralytics_callback(run_id: str, session, training_run):
    """Create an Ultralytics callback that streams metrics to Redis and updates DB."""

    nan_count = [0]  # mutable counter for closure
    NAN_ABORT_THRESHOLD = 3
    loss_history: list[dict] = []
    metric_history: list[dict] = []
    batch_counter = [0]  # Counts up each batch within an epoch
    last_batch_publish = [0.0]  # timestamp of last batch publish
    current_epoch = [0]

    def on_train_batch_end(trainer):
        """Stream batch-level progress so UI doesn't appear frozen during long epochs."""
        import time

        # Reset counter on new epoch
        epoch = trainer.epoch + 1
        if epoch != current_epoch[0]:
            current_epoch[0] = epoch
            batch_counter[0] = 0
        batch_counter[0] += 1

        # Throttle: publish at most every 2 seconds
        now = time.monotonic()
        if now - last_batch_publish[0] < 2.0:
            return
        last_batch_publish[0] = now

        total_epochs = trainer.epochs

        # Total batches = length of train loader
        total_batches = len(trainer.train_loader) if hasattr(trainer, "train_loader") and trainer.train_loader else 0

        # Get current running loss
        batch_losses = {}
        try:
            if hasattr(trainer, "loss_items") and trainer.loss_items is not None:
                import torch
                items = trainer.loss_items
                if isinstance(items, torch.Tensor):
                    items = items.detach().cpu().tolist()
                # Get loss names from the model's label_loss_items
                names = trainer.label_loss_items() if hasattr(trainer, "label_loss_items") else []
                if isinstance(names, list):
                    for i, name in enumerate(names):
                        if i < len(items):
                            batch_losses[name] = round(float(items[i]), 4)
                else:
                    for i, v in enumerate(items if isinstance(items, (list, tuple)) else [items]):
                        batch_losses[f"loss_{i}"] = round(float(v), 4)
            elif hasattr(trainer, "tloss") and trainer.tloss is not None:
                import torch
                tloss = trainer.tloss
                if isinstance(tloss, torch.Tensor):
                    tloss = tloss.detach().cpu().tolist()
                if isinstance(tloss, (list, tuple)):
                    for i, v in enumerate(tloss):
                        batch_losses[f"loss_{i}"] = round(float(v), 4)
                else:
                    batch_losses["total_loss"] = round(float(tloss), 4)
        except Exception:
            pass

        # Check for user-requested stop (every batch, not just every epoch)
        if should_stop_training(run_id):
            trainer.stop = True
            publish_metrics(run_id, {
                "run_id": run_id, "epoch": epoch, "total_epochs": total_epochs,
                "batch": batch_counter[0], "total_batches": total_batches,
                "status": "stopping",
            })
            return

        payload = {
            "run_id": run_id,
            "epoch": epoch,
            "total_epochs": total_epochs,
            "batch": batch_counter[0],
            "total_batches": total_batches,
            "batch_losses": batch_losses,
            "status": "training",
        }
        # Include stored val preview so it persists across batch updates
        try:
            client = get_redis_client()
            preview = client.get(f"waldo:training:preview:{run_id}")
            if preview:
                payload["val_preview"] = preview.decode("ascii") if isinstance(preview, bytes) else preview
        except Exception:
            pass
        publish_metrics(run_id, payload)

    def on_train_epoch_end(trainer):
        metrics = {}
        if hasattr(trainer, "metrics"):
            for k, v in trainer.metrics.items():
                metrics[k] = float(v) if hasattr(v, "__float__") else v

        epoch = trainer.epoch + 1
        total = trainer.epochs

        # Check for user-requested stop
        if should_stop_training(run_id):
            publish_metrics(run_id, {
                "run_id": run_id,
                "epoch": epoch,
                "total_epochs": total,
                "status": "stopping",
                "metrics": metrics,
            })
            trainer.stop = True  # Ultralytics respects this flag
            return

        # Check for NaN loss divergence
        loss_keys = [k for k in metrics if "loss" in k.lower()]
        loss_vals = {k: metrics[k] for k in loss_keys}
        if _has_nan(loss_vals):
            nan_count[0] += 1
            if nan_count[0] >= NAN_ABORT_THRESHOLD:
                publish_metrics(run_id, {
                    "run_id": run_id,
                    "epoch": epoch,
                    "total_epochs": total,
                    "status": "failed",
                    "error": f"Training diverged: NaN loss for {NAN_ABORT_THRESHOLD} consecutive epochs",
                })
                training_run.status = "failed"
                training_run.error_message = f"NaN loss for {NAN_ABORT_THRESHOLD} consecutive epochs"
                session.commit()
                raise RuntimeError(f"Training aborted: NaN loss for {NAN_ABORT_THRESHOLD} consecutive epochs")
        else:
            nan_count[0] = 0

        # Build loss and metric history for charts
        epoch_losses = {k: metrics[k] for k in loss_keys if k in metrics}
        loss_history.append({"epoch": epoch, **epoch_losses})

        metric_keys = [k for k in metrics if "map" in k.lower() or "precision" in k.lower() or "recall" in k.lower()]
        epoch_metrics = {k: metrics[k] for k in metric_keys if k in metrics}
        metric_history.append({"epoch": epoch, **epoch_metrics})

        # Detect overfitting: val loss increasing while train loss decreasing
        overfit_warning = None
        if len(loss_history) >= 10:
            recent = loss_history[-5:]
            earlier = loss_history[-10:-5]
            val_keys = [k for k in loss_keys if "val" in k.lower()]
            for vk in val_keys:
                recent_avg = sum(e.get(vk, 0) for e in recent) / 5
                earlier_avg = sum(e.get(vk, 0) for e in earlier) / 5
                if recent_avg > earlier_avg * 1.05:  # 5% increase
                    overfit_warning = f"Possible overfitting: {vk} increasing ({earlier_avg:.4f} → {recent_avg:.4f})"
                    break

        payload = {
            "run_id": run_id,
            "epoch": epoch,
            "total_epochs": total,
            "metrics": metrics,
            "status": "training",
            "loss_history": loss_history[-50:],  # Last 50 epochs for chart
            "metric_history": metric_history[-50:],
        }
        if overfit_warning:
            payload["warning"] = overfit_warning

        # Send training sample images — these show ground truth annotations
        # and are generated by YOLO at training start (always available)
        try:
            import base64
            from pathlib import Path

            import cv2

            if hasattr(trainer, "save_dir"):
                save_dir = Path(str(trainer.save_dir))

                # Priority: val predictions > val labels > train batches
                candidates = (
                    sorted(save_dir.glob("val_batch*_pred.jpg")) +
                    sorted(save_dir.glob("val_batch*_labels.jpg")) +
                    sorted(save_dir.glob("train_batch*.jpg"))
                )
                if candidates:
                    img = cv2.imread(str(candidates[-1]))
                    if img is not None:
                        h, w = img.shape[:2]
                        if w > 800:
                            scale = 800 / w
                            img = cv2.resize(img, (800, int(h * scale)))
                        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                        encoded = base64.b64encode(buf).decode("ascii")
                        payload["val_preview"] = encoded
                        # Also store separately so batch messages don't overwrite it
                        client = get_redis_client()
                        client.set(f"waldo:training:preview:{run_id}", encoded, ex=3600)
        except Exception:
            pass  # Never fail training over a preview image

        # Publish to Redis
        publish_metrics(run_id, payload)

        # Update DB — persist history so old experiments show curves
        training_run.epoch_current = epoch
        training_run.metrics = metrics
        training_run.loss_history = loss_history[-100:]  # Keep last 100 epochs
        training_run.metric_history = metric_history[-100:]
        if hasattr(trainer, "best_fitness") and trainer.best_fitness is not None:
            training_run.best_metrics = metrics
        session.commit()

    def on_val_end(validator):
        """Send val prediction images immediately after validation."""
        try:
            import base64
            from pathlib import Path

            import cv2

            save_dir = Path(validator.save_dir) if hasattr(validator, "save_dir") else None
            if not save_dir:
                # Try the trainer's save_dir (validator might be nested)
                if hasattr(validator, "args") and hasattr(validator.args, "save_dir"):
                    save_dir = Path(validator.args.save_dir)
            if not save_dir or not save_dir.exists():
                return

            val_imgs = sorted(save_dir.glob("val_batch*_pred.jpg"))
            if not val_imgs:
                return

            img = cv2.imread(str(val_imgs[-1]))
            if img is None:
                return

            h, w = img.shape[:2]
            if w > 800:
                scale = 800 / w
                img = cv2.resize(img, (800, int(h * scale)))
            _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
            preview = base64.b64encode(buf).decode("ascii")

            publish_metrics(run_id, {
                "run_id": run_id,
                "val_preview": preview,
                "status": "training",
            })
        except Exception:
            pass  # Never fail training over a preview

    def on_train_end(trainer):
        payload = {
            "run_id": run_id,
            "epoch": trainer.epochs,
            "total_epochs": trainer.epochs,
            "status": "completed",
            "metrics": {},
            "loss_history": loss_history,
            "metric_history": metric_history,
        }
        if hasattr(trainer, "metrics"):
            for k, v in trainer.metrics.items():
                payload["metrics"][k] = float(v) if hasattr(v, "__float__") else v
        publish_metrics(run_id, payload)

    return {
        "on_train_batch_end": on_train_batch_end,
        "on_train_epoch_end": on_train_epoch_end,
        "on_train_end": on_train_end,
        "on_val_end": on_val_end,
    }
