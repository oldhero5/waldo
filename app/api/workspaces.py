"""Workspace management — create, list, switch workspaces."""
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lib.auth import get_current_user
from lib.db import SessionLocal, User, Workspace, WorkspaceMember

router = APIRouter()


class CreateWorkspaceRequest(BaseModel):
    name: str


class WorkspaceOut(BaseModel):
    id: str
    name: str
    slug: str
    member_count: int
    role: str


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:100]


@router.post("/workspaces", status_code=201, response_model=WorkspaceOut)
def create_workspace(req: CreateWorkspaceRequest, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        slug = _slugify(req.name)
        existing = session.query(Workspace).filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{str(existing.id)[:6]}"

        ws = Workspace(name=req.name, slug=slug)
        session.add(ws)
        session.flush()

        member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="admin")
        session.add(member)
        session.commit()

        return WorkspaceOut(id=str(ws.id), name=ws.name, slug=ws.slug, member_count=1, role="admin")
    finally:
        session.close()


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        memberships = session.query(WorkspaceMember).filter_by(user_id=user.id).all()
        result = []
        for m in memberships:
            ws = session.query(Workspace).filter_by(id=m.workspace_id).first()
            if ws:
                count = session.query(WorkspaceMember).filter_by(workspace_id=ws.id).count()
                result.append(WorkspaceOut(
                    id=str(ws.id), name=ws.name, slug=ws.slug,
                    member_count=count, role=m.role,
                ))
        return result
    finally:
        session.close()
