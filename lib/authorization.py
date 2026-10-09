"""Shared request identity, workspace membership, and exact ownership checks.

JWT callers select a membership with X-Workspace-ID or their oldest membership.
API keys are confined to their persisted workspace and explicit read/write scopes.
Installation administrators still need membership for ordinary workspace routes.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import and_, or_, select

from lib.auth import get_current_user
from lib.db import (
    Annotation,
    ApiKey,
    DeploymentExperiment,
    DeploymentTarget,
    Frame,
    LabelingJob,
    ModelRegistry,
    Project,
    SessionLocal,
    TrainingRun,
    User,
    Video,
    Workspace,
    WorkspaceMember,
)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    auth_kind: Literal["jwt", "api_key"] = "jwt"
    api_key_id: UUID | None = None
    workspace_id: UUID | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class WorkspacePrincipal:
    identity: Principal
    workspace_id: UUID
    role: str

    @property
    def user_id(self) -> UUID:
        return self.identity.user_id


def get_principal(request: Request, user: User = Depends(get_current_user)) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal) or principal.user_id != user.id:
        raise HTTPException(status_code=401, detail="Authenticated principal is required")
    return principal


def require_scope(principal: Principal, scope: Literal["read", "write"]) -> None:
    if principal.auth_kind == "api_key" and scope not in principal.scopes:
        raise HTTPException(status_code=403, detail=f"API key requires {scope} scope")


def _uuid(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(status_code=400, detail="Invalid resource ID") from error


def resolve_workspace(session, principal: Principal, workspace_id: UUID | str | None = None) -> WorkspacePrincipal:
    requested = _uuid(workspace_id) if workspace_id is not None else None
    if principal.auth_kind == "api_key":
        key = session.query(ApiKey).populate_existing().filter_by(id=principal.api_key_id).first()
        if key is None or key.user_id != principal.user_id or key.workspace_id != principal.workspace_id:
            raise HTTPException(status_code=401, detail="API key is no longer valid")
        if key.expires_at:
            expiry = key.expires_at if key.expires_at.tzinfo is not None else key.expires_at.replace(tzinfo=UTC)
            if expiry <= datetime.now(UTC):
                raise HTTPException(status_code=401, detail="API key expired")
        principal = Principal(
            user_id=key.user_id,
            auth_kind="api_key",
            api_key_id=key.id,
            workspace_id=key.workspace_id,
            scopes=frozenset(key.scopes or []),
        )
        if principal.workspace_id is None or (requested is not None and requested != principal.workspace_id):
            raise HTTPException(status_code=403, detail="API key is restricted to its workspace")
        requested = principal.workspace_id
    query = (
        session.query(WorkspaceMember)
        .populate_existing()
        .join(Workspace)
        .filter(WorkspaceMember.user_id == principal.user_id)
    )
    if requested is not None:
        query = query.filter(WorkspaceMember.workspace_id == requested)
    member = query.order_by(WorkspaceMember.joined_at, WorkspaceMember.id).first()
    if member is None:
        raise HTTPException(status_code=403, detail="Workspace membership is required")
    return WorkspacePrincipal(identity=principal, workspace_id=member.workspace_id, role=member.role)


def get_workspace_principal(
    principal: Principal = Depends(get_principal),
    workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
) -> WorkspacePrincipal:
    session = SessionLocal()
    try:
        return resolve_workspace(session, principal, workspace_id)
    finally:
        session.close()


def require_workspace_role(principal: WorkspacePrincipal, *roles: str) -> None:
    if principal.role not in roles:
        raise HTTPException(status_code=403, detail="Workspace role does not permit this action")
    require_scope(principal.identity, "write")


def scope_projects(query, principal: WorkspacePrincipal):
    return query.filter(Project.workspace_id == principal.workspace_id)


def require_project(session, principal: WorkspacePrincipal, project_id: UUID | str) -> Project:
    project = scope_projects(session.query(Project), principal).filter(Project.id == _uuid(project_id)).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def require_workspace_editor(principal: WorkspacePrincipal = Depends(get_workspace_principal)) -> WorkspacePrincipal:
    require_workspace_role(principal, "admin", "editor")
    return principal


def scope_resources(query, model, principal: WorkspacePrincipal):
    """Scope resource queries through persisted ownership, including legacy video-backed jobs."""
    if hasattr(model, "workspace_id"):
        return query.filter(model.workspace_id == principal.workspace_id)
    projects = select(Project.id).where(Project.workspace_id == principal.workspace_id)
    videos = select(Video.id).where(Video.project_id.in_(projects))
    jobs = select(LabelingJob.id).where(
        or_(
            LabelingJob.project_id.in_(projects),
            and_(LabelingJob.project_id.is_(None), LabelingJob.video_id.in_(videos)),
        ),
        or_(LabelingJob.video_id.is_(None), LabelingJob.video_id.in_(videos)),
    )
    runs = select(TrainingRun.id).where(
        TrainingRun.project_id.in_(projects),
        or_(TrainingRun.job_id.is_(None), TrainingRun.job_id.in_(jobs)),
    )
    if model is LabelingJob:
        return query.filter(model.id.in_(jobs))
    if model is TrainingRun:
        return query.filter(model.id.in_(runs))
    if model is ModelRegistry:
        matching_runs = (
            select(TrainingRun.id)
            .where(TrainingRun.id.in_(runs), TrainingRun.project_id == ModelRegistry.project_id)
            .correlate(ModelRegistry)
        )
        return query.filter(ModelRegistry.project_id.in_(projects), ModelRegistry.training_run_id.in_(matching_runs))
    if model is DeploymentExperiment:
        matching_runs = (
            select(TrainingRun.id)
            .where(TrainingRun.id.in_(runs), TrainingRun.project_id == ModelRegistry.project_id)
            .correlate(ModelRegistry)
        )
        owned_models = select(ModelRegistry.id).where(
            ModelRegistry.project_id.in_(projects), ModelRegistry.training_run_id.in_(matching_runs)
        )
        targets = select(DeploymentTarget.id).where(DeploymentTarget.workspace_id == principal.workspace_id)
        return query.filter(
            DeploymentExperiment.champion_model_id.in_(owned_models),
            DeploymentExperiment.challenger_model_id.in_(owned_models),
            or_(DeploymentExperiment.target_id.is_(None), DeploymentExperiment.target_id.in_(targets)),
        )
    if model is Video:
        return query.filter(Video.project_id.in_(projects))
    if model is Frame:
        return query.filter(Frame.video_id.in_(videos))
    if model is Annotation:
        frames = select(Frame.id).where(Frame.video_id.in_(videos))
        return query.filter(Annotation.frame_id.in_(frames), Annotation.job_id.in_(jobs))
    raise RuntimeError(f"No ownership policy for {model.__name__}")


def require_resource(session, principal: WorkspacePrincipal, model, resource_id: UUID | str):
    resource = scope_resources(session.query(model), model, principal).filter(model.id == _uuid(resource_id)).first()
    if resource is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return resource


def register_task_owner(task_id: str, principal: WorkspacePrincipal) -> None:
    """Persist ownership before publishing ephemeral work to the broker."""
    from lib.redis_client import get_redis

    try:
        get_redis().setex(f"waldo:task:workspace:{task_id}", 86400, str(principal.workspace_id))
    except Exception as error:
        raise HTTPException(status_code=503, detail="Task ownership store unavailable") from error


def require_task_owner(task_id: str, principal: WorkspacePrincipal) -> None:
    session = SessionLocal()
    try:
        for model in (LabelingJob, TrainingRun):
            if scope_resources(session.query(model), model, principal).filter(model.celery_task_id == task_id).first():
                return
    finally:
        session.close()
    from lib.redis_client import get_redis

    try:
        owner = get_redis().get(f"waldo:task:workspace:{task_id}")
    except Exception as error:
        raise HTTPException(status_code=503, detail="Task ownership store unavailable") from error
    if isinstance(owner, bytes):
        owner = owner.decode()
    if owner != str(principal.workspace_id):
        raise HTTPException(status_code=404, detail="Task not found")
