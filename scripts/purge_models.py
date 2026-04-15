"""Wipe trained models, training runs, and inference artifacts — keep datasets.

Removes:
  DB: inference_logs, demo_feedback, deployment_experiments, deployment_targets,
      edge_devices, comparison_runs, model_registry, training_runs
  MinIO: models/, results/ prefixes
  Disk: runs/segment/, training/, top-level yolo*-seg.pt baseline weights

Keeps:
  DB: videos, frames, labeling_jobs, annotations, projects, workspaces, users,
      saved_workflows
  MinIO: frames/, videos/, feedback/, workflows/
  Disk: models/ (SAM 3.1 / SigLIP components needed for labeling)

Usage:
  uv run python scripts/purge_models.py --dry-run   # show what would be deleted
  uv run python scripts/purge_models.py --execute   # actually delete
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from sqlalchemy import text

from lib.config import settings
from lib.db import get_session
from lib.storage import get_client

REPO = Path(__file__).resolve().parent.parent

# Order matters: FK dependencies first.
DB_TABLES_TO_WIPE = [
    "inference_logs",
    "demo_feedback",
    "deployment_experiments",
    "deployment_targets",
    "edge_devices",
    "comparison_runs",
    "model_registry",
    "training_runs",
]

MINIO_PREFIXES_TO_WIPE = ["models/", "results/"]

DISK_PATHS_TO_WIPE = [
    REPO / "runs" / "segment",
    REPO / "training",
    *REPO.glob("yolo*-seg.pt"),
]


def count_rows(session, table: str) -> int:
    # Table name comes from DB_TABLES_TO_WIPE, not user input.
    return session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0  # noqa: S608


def wipe_db(dry_run: bool) -> None:
    print("\n=== DATABASE ===")
    session = get_session()
    try:
        for table in DB_TABLES_TO_WIPE:
            try:
                n = count_rows(session, table)
            except Exception as e:
                print(f"  [skip] {table}: {e}")
                session.rollback()
                continue
            if n == 0:
                print(f"  [empty] {table}")
                continue
            if dry_run:
                print(f"  [would delete] {table}: {n} rows")
            else:
                session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
                print(f"  [deleted]     {table}: {n} rows")
        if not dry_run:
            session.commit()
    finally:
        session.close()


def wipe_minio(dry_run: bool) -> None:
    print("\n=== MINIO ===")
    client = get_client()
    bucket = settings.minio_bucket
    for prefix in MINIO_PREFIXES_TO_WIPE:
        try:
            objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
        except Exception as e:
            print(f"  [skip] {prefix}: {e}")
            continue
        if not objects:
            print(f"  [empty] {prefix}")
            continue
        total_bytes = sum((o.size or 0) for o in objects)
        if dry_run:
            print(f"  [would delete] {prefix}: {len(objects)} objects, {total_bytes / 1e6:.1f} MB")
        else:
            for obj in objects:
                client.remove_object(bucket, obj.object_name)
            print(f"  [deleted]     {prefix}: {len(objects)} objects, {total_bytes / 1e6:.1f} MB")


def wipe_disk(dry_run: bool) -> None:
    print("\n=== DISK ===")
    for path in DISK_PATHS_TO_WIPE:
        if not path.exists():
            print(f"  [missing] {path.relative_to(REPO)}")
            continue
        if path.is_dir():
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        else:
            size = path.stat().st_size
        rel = path.relative_to(REPO)
        if dry_run:
            print(f"  [would delete] {rel}: {size / 1e6:.1f} MB")
        else:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            print(f"  [deleted]     {rel}: {size / 1e6:.1f} MB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    group.add_argument("--execute", action="store_true", help="Actually delete")
    args = parser.parse_args()

    dry_run = args.dry_run
    mode = "DRY RUN" if dry_run else "EXECUTE"
    print(f"=== PURGE MODELS ({mode}) ===")
    print("Keeps: videos, frames, labeling_jobs, annotations, projects, workflows")
    print("Wipes: trained models, training runs, inference logs, comparisons, model disk/minio")

    wipe_db(dry_run)
    wipe_minio(dry_run)
    wipe_disk(dry_run)

    print("\nDone.")
    if dry_run:
        print("Re-run with --execute to actually delete.")


if __name__ == "__main__":
    main()
