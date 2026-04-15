import tempfile
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from PIL import Image
from pydantic import BaseModel

from labeler.frame_extractor import get_video_metadata
from lib.auth import get_current_user
from lib.db import Frame, LabelingJob, Project, SessionLocal, Video
from lib.storage import get_download_url, upload_bytes, upload_file
from lib.tasks import label_video

router = APIRouter(dependencies=[Depends(get_current_user)])


class UploadResponse(BaseModel):
    video_id: str
    project_id: str
    filename: str
    minio_key: str


class BatchUploadResponse(BaseModel):
    videos: list[UploadResponse]
    project_id: str


class ProjectOut(BaseModel):
    id: str
    name: str
    video_count: int
    created_at: str


class VideoOut(BaseModel):
    id: str
    filename: str
    fps: float | None
    duration_s: float | None
    width: int | None
    height: int | None
    frame_count: int | None
    created_at: str


def _get_or_create_project(session, project_name: str) -> Project:
    """Get an existing project by name or create a new one."""
    project = session.query(Project).filter_by(name=project_name).first()
    if not project:
        project = Project(name=project_name)
        session.add(project)
        session.commit()
    return project


def _auto_label_if_applicable(session, project: Project, video: Video) -> None:
    """If this project has a completed labeling job, label the new video and merge into that job.

    Instead of creating a separate job per video (which fragments the dataset),
    we create a labeling job that, upon completion, merges its annotations into
    the existing master job. This keeps the dataset as a single coherent unit.
    """
    try:
        # Find the master labeling job for this project
        master_job = (
            session.query(LabelingJob)
            .filter_by(project_id=project.id, status="completed")
            .order_by(LabelingJob.created_at.desc())
            .first()
        )
        if not master_job:
            # Fallback: search by text_prompt matching project name
            master_job = (
                session.query(LabelingJob)
                .filter(
                    LabelingJob.status == "completed",
                    LabelingJob.text_prompt.ilike(f"%{project.name}%"),
                )
                .order_by(LabelingJob.created_at.desc())
                .first()
            )
        if not master_job or not master_job.text_prompt:
            return

        # Create a child labeling job for this video
        # Tag it with merge_into so the pipeline knows to merge results back
        child_job = LabelingJob(
            video_id=video.id,
            project_id=project.id,
            text_prompt=master_job.text_prompt,
            class_prompts=master_job.class_prompts,
            task_type=master_job.task_type or "segment",
            status="pending",
        )
        session.add(child_job)
        session.commit()
        session.refresh(child_job)

        # Queue the labeling task
        task = label_video.delay(str(child_job.id), merge_into=str(master_job.id))
        child_job.celery_task_id = task.id
        session.commit()
    except Exception:
        pass  # Don't fail the upload if auto-labeling fails


class LinkVideosRequest(BaseModel):
    video_ids: list[str]
    target_project_name: str


class LinkVideosResponse(BaseModel):
    linked: int
    auto_labeled: int


@router.post("/link-videos", response_model=LinkVideosResponse)
def link_existing_videos(req: LinkVideosRequest):
    """Copy/link existing videos from other projects into a target project and auto-label them."""
    session = SessionLocal()
    try:
        target_project = _get_or_create_project(session, req.target_project_name)
        linked = 0
        auto_labeled = 0

        for vid_id in req.video_ids:
            source_video = session.query(Video).filter_by(id=vid_id).first()
            if not source_video:
                continue
            if str(source_video.project_id) == str(target_project.id):
                continue  # Already in target

            # Check for duplicate by filename
            existing = (
                session.query(Video)
                .filter_by(
                    project_id=target_project.id,
                    filename=source_video.filename,
                )
                .first()
            )
            if existing:
                continue

            # Create a new Video record in the target project pointing to same MinIO key
            new_video = Video(
                id=uuid.uuid4(),
                project_id=target_project.id,
                filename=source_video.filename,
                minio_key=source_video.minio_key,
                fps=source_video.fps,
                duration_s=source_video.duration_s,
                width=source_video.width,
                height=source_video.height,
                frame_count=source_video.frame_count,
            )
            session.add(new_video)
            session.commit()
            linked += 1

            # Auto-label
            _auto_label_if_applicable(session, target_project, new_video)
            auto_labeled += 1

        return LinkVideosResponse(linked=linked, auto_labeled=auto_labeled)
    finally:
        session.close()


MAX_VIDEO_SIZE_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB


async def _upload_single_video(session, project: Project, file: UploadFile) -> UploadResponse:
    """Handle uploading a single video file: save to temp, extract metadata, upload to MinIO, create DB record."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
        content = await file.read()
        if len(content) > MAX_VIDEO_SIZE_BYTES:
            raise HTTPException(status_code=413, detail=f"Video exceeds 10 GB limit ({len(content) / 1024**3:.1f} GB)")
        tmp.write(content)
        tmp_path = tmp.name

    try:
        meta = get_video_metadata(tmp_path)

        # Duplicate detection: same filename + same duration + same resolution = likely duplicate
        existing = (
            session.query(Video)
            .filter_by(
                project_id=project.id,
                filename=file.filename,
            )
            .first()
        )
        if existing and existing.duration_s and meta.duration_s:
            if abs((existing.duration_s or 0) - (meta.duration_s or 0)) < 0.5:
                # Same file — return existing without re-uploading
                return UploadResponse(
                    video_id=str(existing.id),
                    project_id=str(project.id),
                    filename=existing.filename,
                    minio_key=existing.minio_key,
                )

        video_id = uuid.uuid4()
        minio_key = f"videos/{video_id}/{file.filename}"
        upload_file(minio_key, tmp_path)

        video = Video(
            id=video_id,
            project_id=project.id,
            filename=file.filename,
            minio_key=minio_key,
            fps=meta.fps,
            duration_s=meta.duration_s,
            width=meta.width,
            height=meta.height,
            frame_count=meta.frame_count,
        )
        session.add(video)
        session.commit()

        # Auto-label: if this project has a completed labeling job, start labeling
        # the new video with the same prompt and task type automatically
        _auto_label_if_applicable(session, project, video)

        return UploadResponse(
            video_id=str(video.id),
            project_id=str(project.id),
            filename=file.filename,
            minio_key=minio_key,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/upload", status_code=201, response_model=UploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    project_name: str = Query("default"),
):
    session = SessionLocal()
    try:
        project = _get_or_create_project(session, project_name)
        return await _upload_single_video(session, project, file)
    finally:
        session.close()


@router.post("/upload/batch", status_code=201, response_model=BatchUploadResponse)
async def upload_videos_batch(
    files: list[UploadFile] = File(...),
    project_name: str = Query("default"),
):
    session = SessionLocal()
    try:
        project = _get_or_create_project(session, project_name)
        results = []
        for file in files:
            result = await _upload_single_video(session, project, file)
            results.append(result)
        return BatchUploadResponse(videos=results, project_id=str(project.id))
    finally:
        session.close()


@router.get("/projects", response_model=list[ProjectOut])
def list_projects():
    session = SessionLocal()
    try:
        projects = session.query(Project).all()
        return [
            ProjectOut(
                id=str(p.id),
                name=p.name,
                video_count=len(p.videos),
                created_at=p.created_at.isoformat(),
            )
            for p in projects
        ]
    finally:
        session.close()


class ImageUploadResponse(BaseModel):
    frame_ids: list[str]
    urls: list[str]
    project_id: str


_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".tif"}


def _get_or_create_images_video(session, project: Project) -> Video:
    """Get or create a placeholder 'images' Video entry for standalone image uploads."""
    video = session.query(Video).filter_by(project_id=project.id, filename="__standalone_images__").first()
    if not video:
        video = Video(
            project_id=project.id,
            filename="__standalone_images__",
            minio_key=f"images/{project.id}/__standalone_images__",
            fps=None,
            duration_s=None,
            width=None,
            height=None,
            frame_count=0,
        )
        session.add(video)
        session.commit()
    return video


@router.post("/upload/images", status_code=201, response_model=ImageUploadResponse)
async def upload_images(
    files: list[UploadFile] = File(...),
    project_name: str = Form("default"),
):
    """Upload one or more images to a project as standalone Frame records."""
    session = SessionLocal()
    try:
        project = _get_or_create_project(session, project_name)
        images_video = _get_or_create_images_video(session, project)

        # Determine next frame_number offset
        max_frame = (
            session.query(Frame.frame_number)
            .filter_by(video_id=images_video.id)
            .order_by(Frame.frame_number.desc())
            .first()
        )
        next_number = (max_frame[0] + 1) if max_frame else 0

        frame_ids: list[str] = []
        urls: list[str] = []

        for i, file in enumerate(files):
            ext = Path(file.filename).suffix.lower()
            if ext not in _ALLOWED_IMAGE_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported image type '{ext}' for file '{file.filename}'. "
                    f"Allowed: {', '.join(sorted(_ALLOWED_IMAGE_EXTENSIONS))}",
                )

            content = await file.read()

            # Read image dimensions
            img = Image.open(BytesIO(content))
            width, height = img.size

            frame_id = uuid.uuid4()
            content_type = file.content_type or "image/jpeg"
            minio_key = f"images/{project.id}/{frame_id}{ext}"
            upload_bytes(minio_key, content, content_type=content_type)

            frame = Frame(
                id=frame_id,
                video_id=images_video.id,
                frame_number=next_number + i,
                timestamp_s=0.0,
                minio_key=minio_key,
                width=width,
                height=height,
            )
            session.add(frame)
            frame_ids.append(str(frame_id))
            urls.append(get_download_url(minio_key))

        # Update frame_count on the placeholder video
        images_video.frame_count = (images_video.frame_count or 0) + len(files)
        session.commit()

        return ImageUploadResponse(
            frame_ids=frame_ids,
            urls=urls,
            project_id=str(project.id),
        )
    finally:
        session.close()


@router.get("/projects/{project_id}/videos", response_model=list[VideoOut])
def list_project_videos(project_id: str):
    session = SessionLocal()
    try:
        videos = session.query(Video).filter_by(project_id=project_id).all()
        return [
            VideoOut(
                id=str(v.id),
                filename=v.filename,
                fps=v.fps,
                duration_s=v.duration_s,
                width=v.width,
                height=v.height,
                frame_count=v.frame_count,
                created_at=v.created_at.isoformat(),
            )
            for v in videos
        ]
    finally:
        session.close()
