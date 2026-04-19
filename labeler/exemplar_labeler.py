"""Exemplar/click labeling pipeline — uses Sam3TrackerVideoModel for point-prompt tracking."""

import tempfile
from pathlib import Path

from PIL import Image

from labeler.frame_extractor import extract_frames
from labeler.pipeline import _update_job, convert_and_store
from labeler.sam3_engine import get_engine
from lib.db import Frame, LabelingJob, SessionLocal
from lib.storage import download_file, upload_file


def run_exemplar_pipeline(celery_task, job_id: str) -> dict:
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).one()
        video = job.video
        point_prompts = job.point_prompts  # {"frame_idx": int, "points": [[x,y],...], "labels": [1,0,...]}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Phase 1: Extract frames
            _update_job(session, job, status="extracting")
            celery_task.update_state(state="EXTRACTING")

            video_path = tmpdir / video.filename
            download_file(video.minio_key, video_path)

            frames_dir = tmpdir / "frames"
            frame_infos = extract_frames(video_path, frames_dir)
            _update_job(session, job, total_frames=len(frame_infos))

            # Upload frames to MinIO and record in DB
            db_frames: list[Frame] = []
            for fi in frame_infos:
                minio_key = f"frames/{job.id}/{fi.file_path.name}"
                upload_file(minio_key, fi.file_path)

                db_frame = Frame(
                    video_id=video.id,
                    frame_number=fi.frame_number,
                    timestamp_s=fi.timestamp_s,
                    minio_key=minio_key,
                    phash=fi.phash,
                    width=fi.width,
                    height=fi.height,
                )
                session.add(db_frame)
                db_frames.append(db_frame)
            session.commit()

            # Phase 2: SAM 3 point-prompt segmentation
            _update_job(session, job, status="labeling")
            celery_task.update_state(state="LABELING")

            engine = get_engine()
            images = [Image.open(fi.file_path) for fi in frame_infos]
            seg_results = engine.segment_frames_with_points(
                images,
                prompt_frame_idx=point_prompts["frame_idx"],
                points=point_prompts["points"],
                labels=point_prompts["labels"],
            )

            # Phase 3: Convert + store + package
            _update_job(session, job, status="converting")
            celery_task.update_state(state="CONVERTING")

            class_name = job.text_prompt or "object"
            class_names = [class_name]
            result_key = convert_and_store(session, job, seg_results, db_frames, frame_infos, class_names, tmpdir)

            _update_job(session, job, status="completed", result_minio_key=result_key, progress=1.0)
            return {"status": "completed", "result_minio_key": result_key}

    except Exception as e:
        session.rollback()
        try:
            job = session.query(LabelingJob).filter_by(id=job_id).one()
            _update_job(session, job, status="failed", error_message=str(e))
        except Exception:
            pass
        raise
    finally:
        session.close()
