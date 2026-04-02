import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from lib.config import settings


class Base(DeclarativeBase):
    pass


# ── Auth & Workspaces ────────────────────────────────────────

class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    members = relationship("WorkspaceMember", back_populates="workspace")
    projects = relationship("Project", back_populates="workspace")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    avatar_url = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    memberships = relationship("WorkspaceMember", back_populates="user")


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    role = Column(String(20), nullable=False, default="annotator")  # admin, annotator, reviewer, viewer
    joined_at = Column(DateTime, default=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="members")
    user = relationship("User", back_populates="memberships")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    key_hash = Column(String(255), nullable=False)
    key_prefix = Column(String(8), nullable=False)
    scopes = Column(JSON, default=list)
    last_used = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Projects & Data ──────────────────────────────────────────

class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True)  # nullable for migration
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="projects")
    videos = relationship("Video", back_populates="project")


class Video(Base):
    __tablename__ = "videos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    filename = Column(String(512), nullable=False)
    minio_key = Column(String(1024), nullable=False)
    fps = Column(Float)
    duration_s = Column(Float)
    width = Column(Integer)
    height = Column(Integer)
    frame_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="videos")
    frames = relationship("Frame", back_populates="video")
    labeling_jobs = relationship("LabelingJob", back_populates="video")


class Frame(Base):
    __tablename__ = "frames"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id"), nullable=False)
    frame_number = Column(Integer, nullable=False)
    timestamp_s = Column(Float, nullable=False)
    minio_key = Column(String(1024), nullable=False)
    phash = Column(String(64))
    width = Column(Integer)
    height = Column(Integer)

    video = relationship("Video", back_populates="frames")
    annotations = relationship("Annotation", back_populates="frame")


class LabelingJob(Base):
    __tablename__ = "labeling_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id"), nullable=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    text_prompt = Column(Text, nullable=True)
    prompt_type = Column(String(20), default="text")
    point_prompts = Column(JSON, nullable=True)
    class_prompts = Column(JSON, nullable=True)
    task_type = Column(String(20), default="segment")
    status = Column(String(50), default="pending")
    progress = Column(Float, default=0.0)
    total_frames = Column(Integer, default=0)
    processed_frames = Column(Integer, default=0)
    result_minio_key = Column(String(1024))
    error_message = Column(Text)
    celery_task_id = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    video = relationship("Video", back_populates="labeling_jobs")
    project = relationship("Project")
    annotations = relationship("Annotation", back_populates="job")


class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    frame_id = Column(UUID(as_uuid=True), ForeignKey("frames.id"), nullable=False)
    job_id = Column(UUID(as_uuid=True), ForeignKey("labeling_jobs.id"), nullable=False)
    class_name = Column(String(255), nullable=False)
    class_index = Column(Integer, nullable=False)
    polygon = Column(JSON, nullable=False)
    bbox = Column(JSON, nullable=True)
    confidence = Column(Float)
    status = Column(String(20), default="pending")

    frame = relationship("Frame", back_populates="annotations")
    job = relationship("LabelingJob", back_populates="annotations")


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    job_id = Column(UUID(as_uuid=True), ForeignKey("labeling_jobs.id"), nullable=True)
    name = Column(String(255), nullable=False)
    task_type = Column(String(20), nullable=False, default="segment")
    model_variant = Column(String(100), nullable=False)
    hyperparameters = Column(JSON, default=dict)
    status = Column(String(50), default="queued")
    epoch_current = Column(Integer, default=0)
    total_epochs = Column(Integer, default=100)
    metrics = Column(JSON, default=dict)
    best_metrics = Column(JSON, default=dict)
    loss_history = Column(JSON, default=list)   # [{epoch, train/box_loss, val/box_loss, ...}, ...]
    metric_history = Column(JSON, default=list)  # [{epoch, metrics/precision(B), ...}, ...]
    dataset_minio_key = Column(String(1024))
    checkpoint_minio_key = Column(String(1024))
    best_weights_minio_key = Column(String(1024))
    error_message = Column(Text)
    celery_task_id = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    project = relationship("Project")
    job = relationship("LabelingJob")
    models = relationship("ModelRegistry", back_populates="training_run")


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    training_run_id = Column(UUID(as_uuid=True), ForeignKey("training_runs.id"), nullable=False)
    name = Column(String(255), nullable=False)
    task_type = Column(String(20), nullable=False)
    model_variant = Column(String(100), nullable=False)
    version = Column(Integer, default=1)
    weights_minio_key = Column(String(1024), nullable=False)
    metrics = Column(JSON, default=dict)
    export_formats = Column(JSON, default=dict)
    class_names = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project")
    training_run = relationship("TrainingRun", back_populates="models")


class SavedWorkflow(Base):
    __tablename__ = "saved_workflows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    graph = Column(JSON, nullable=False)
    is_deployed = Column(Boolean, default=False)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoFeedback(Base):
    """Stores false-positive / correction feedback from the Demo page."""
    __tablename__ = "demo_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id = Column(UUID(as_uuid=True), ForeignKey("model_registry.id"), nullable=True)
    class_name = Column(String(255), nullable=False)
    bbox = Column(JSON, nullable=False)  # [x1, y1, x2, y2] in source pixels
    polygon = Column(JSON, nullable=True)  # mask polygon if available
    confidence = Column(Float, nullable=True)
    track_id = Column(Integer, nullable=True)
    frame_index = Column(Integer, nullable=True)
    timestamp_s = Column(Float, nullable=True)
    feedback_type = Column(String(30), default="false_positive")  # false_positive, wrong_class, missed
    corrected_class = Column(String(255), nullable=True)  # if wrong_class, what should it be
    source_filename = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


engine = create_engine(settings.postgres_dsn)
SessionLocal = sessionmaker(bind=engine)


def get_session() -> Session:
    return SessionLocal()
