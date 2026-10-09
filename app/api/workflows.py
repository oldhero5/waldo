"""Workflow API — create, save, run, and deploy visual ML pipelines."""

import asyncio
import re
from uuid import UUID, uuid4

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from lib.authorization import (
    WorkspacePrincipal,
    get_workspace_principal,
    require_scope,
    require_workspace_editor,
    scope_resources,
)
from lib.db import SavedWorkflow, SessionLocal
from lib.workflow_engine import execute_workflow, get_block_schemas, validate_workflow

router = APIRouter()


class WorkflowGraph(BaseModel):
    nodes: list[dict]
    edges: list[dict]


class WorkflowRunRequest(BaseModel):
    graph: WorkflowGraph


class WorkflowRunResponse(BaseModel):
    result: dict | list | str | None = None
    metadata: dict = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class SaveWorkflowRequest(BaseModel):
    name: str
    description: str = ""
    graph: WorkflowGraph


class SavedWorkflowOut(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None
    block_count: int
    is_deployed: bool
    created_at: str


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:100] or "workflow"


def _validate(graph: dict) -> None:
    try:
        validate_workflow(graph)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(status_code=400, detail=f"Invalid workflow: {error}") from error


def _saved_in_workspace(session, principal: WorkspacePrincipal, identifier: str) -> SavedWorkflow:
    query = scope_resources(session.query(SavedWorkflow), SavedWorkflow, principal)
    workflow = query.filter_by(slug=identifier).first()
    if workflow is None:
        try:
            workflow_id = UUID(identifier)
        except ValueError:
            workflow_id = None
        if workflow_id is not None:
            workflow = query.filter_by(id=workflow_id).first()
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


# ── Block catalog ────────────────────────────────────────────


@router.get("/workflows/blocks")
def list_blocks(principal: WorkspacePrincipal = Depends(get_workspace_principal)):
    """Return all available workflow block types with their schemas."""
    require_scope(principal.identity, "read")
    return {"blocks": get_block_schemas()}


# ── CRUD ─────────────────────────────────────────────────────


@router.post("/workflows", status_code=201, response_model=SavedWorkflowOut)
def save_workflow(req: SaveWorkflowRequest, principal: WorkspacePrincipal = Depends(require_workspace_editor)):
    _validate(req.graph.model_dump())
    session = SessionLocal()
    try:
        slug = _slugify(req.name)
        # Check for duplicate slug
        existing = session.query(SavedWorkflow).filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{uuid4().hex[:8]}"

        wf = SavedWorkflow(
            name=req.name,
            slug=slug,
            description=req.description,
            graph=req.graph.model_dump(),
            workspace_id=principal.workspace_id,
        )
        session.add(wf)
        session.commit()
        session.refresh(wf)

        return SavedWorkflowOut(
            id=str(wf.id),
            name=wf.name,
            slug=wf.slug,
            description=wf.description,
            block_count=len(req.graph.nodes),
            is_deployed=wf.is_deployed,
            created_at=wf.created_at.isoformat(),
        )
    finally:
        session.close()


@router.get("/workflows/saved", response_model=list[SavedWorkflowOut])
def list_saved_workflows(principal: WorkspacePrincipal = Depends(get_workspace_principal)):
    require_scope(principal.identity, "read")
    session = SessionLocal()
    try:
        wfs = (
            scope_resources(session.query(SavedWorkflow), SavedWorkflow, principal)
            .order_by(SavedWorkflow.created_at.desc())
            .all()
        )
        return [
            SavedWorkflowOut(
                id=str(wf.id),
                name=wf.name,
                slug=wf.slug,
                description=wf.description,
                block_count=len(wf.graph.get("nodes", [])) if wf.graph else 0,
                is_deployed=wf.is_deployed,
                created_at=wf.created_at.isoformat(),
            )
            for wf in wfs
        ]
    finally:
        session.close()


@router.get("/workflows/saved/{slug}")
def get_saved_workflow(slug: str, principal: WorkspacePrincipal = Depends(get_workspace_principal)):
    require_scope(principal.identity, "read")
    session = SessionLocal()
    try:
        wf = _saved_in_workspace(session, principal, slug)
        return {
            "id": str(wf.id),
            "name": wf.name,
            "slug": wf.slug,
            "description": wf.description,
            "graph": wf.graph,
            "is_deployed": wf.is_deployed,
            "created_at": wf.created_at.isoformat(),
        }
    finally:
        session.close()


@router.put("/workflows/saved/{slug}", response_model=SavedWorkflowOut)
def update_saved_workflow(
    slug: str, req: SaveWorkflowRequest, principal: WorkspacePrincipal = Depends(require_workspace_editor)
):
    _validate(req.graph.model_dump())
    session = SessionLocal()
    try:
        wf = _saved_in_workspace(session, principal, slug)
        wf.name = req.name
        wf.description = req.description
        wf.graph = req.graph.model_dump()
        # Keep identity and endpoint stable when editing a deployed workflow.
        session.commit()
        session.refresh(wf)
        return SavedWorkflowOut(
            id=str(wf.id),
            name=wf.name,
            slug=wf.slug,
            description=wf.description,
            block_count=len(req.graph.nodes),
            is_deployed=wf.is_deployed,
            created_at=wf.created_at.isoformat(),
        )
    finally:
        session.close()


@router.delete("/workflows/saved/{slug}")
def delete_workflow(slug: str, principal: WorkspacePrincipal = Depends(require_workspace_editor)):
    session = SessionLocal()
    try:
        wf = scope_resources(session.query(SavedWorkflow), SavedWorkflow, principal).filter_by(slug=slug).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        session.delete(wf)
        session.commit()
        return {"status": "deleted", "slug": slug}
    finally:
        session.close()


# ── Deploy ───────────────────────────────────────────────────


@router.post("/workflows/saved/{slug}/deploy")
def deploy_workflow(slug: str, principal: WorkspacePrincipal = Depends(require_workspace_editor)):
    session = SessionLocal()
    try:
        wf = scope_resources(session.query(SavedWorkflow), SavedWorkflow, principal).filter_by(slug=slug).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        _validate(wf.graph)
        wf.is_deployed = True
        session.commit()
        return {
            "status": "deployed",
            "slug": slug,
            "endpoint": f"/api/v1/workflows/serve/{slug}",
            "curl": f'curl -X POST http://localhost:8000/api/v1/workflows/serve/{slug} -F "file=@image.jpg"',
        }
    finally:
        session.close()


@router.post("/workflows/serve/{slug}", response_model=WorkflowRunResponse)
async def serve_workflow(
    slug: str, file: UploadFile = File(...), principal: WorkspacePrincipal = Depends(require_workspace_editor)
):
    """Run a deployed workflow with an uploaded image."""
    session = SessionLocal()
    try:
        wf = (
            scope_resources(session.query(SavedWorkflow), SavedWorkflow, principal)
            .filter_by(slug=slug, is_deployed=True)
            .first()
        )
        if not wf:
            raise HTTPException(status_code=404, detail="Deployed workflow not found")
        graph = wf.graph
        _validate(graph)
    finally:
        session.close()

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image")

    def _run():
        return execute_workflow(graph, initial_inputs={"__image__": image}, principal=principal)

    result = await asyncio.to_thread(_run)
    return WorkflowRunResponse(**result)


# ── Run (ad-hoc) ─────────────────────────────────────────────


@router.post("/workflows/run", response_model=WorkflowRunResponse)
async def run_workflow_inline(
    req: WorkflowRunRequest, principal: WorkspacePrincipal = Depends(require_workspace_editor)
):
    _validate(req.graph.model_dump())

    def _run():
        return execute_workflow(req.graph.model_dump(), principal=principal)

    result = await asyncio.to_thread(_run)
    return WorkflowRunResponse(**result)


@router.post("/workflows/run/image", response_model=WorkflowRunResponse)
async def run_workflow_with_image(
    graph: str, file: UploadFile = File(...), principal: WorkspacePrincipal = Depends(require_workspace_editor)
):
    import json

    try:
        graph_data = json.loads(graph)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid graph JSON")

    _validate(graph_data)
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image")

    def _run():
        return execute_workflow(graph_data, initial_inputs={"__image__": image}, principal=principal)

    result = await asyncio.to_thread(_run)
    return WorkflowRunResponse(**result)
