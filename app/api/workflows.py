"""Workflow API — create, save, run, and deploy visual ML pipelines."""
import asyncio
import re

import cv2
import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from lib.db import SavedWorkflow, SessionLocal
from lib.workflow_engine import execute_workflow, get_block_schemas

router = APIRouter()


class WorkflowGraph(BaseModel):
    nodes: list[dict]
    edges: list[dict]


class WorkflowRunRequest(BaseModel):
    graph: WorkflowGraph


class WorkflowRunResponse(BaseModel):
    result: dict | list | str | None = None
    metadata: dict = {}
    errors: list[str] = []


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
    return slug[:100]


# ── Block catalog ────────────────────────────────────────────

@router.get("/workflows/blocks")
def list_blocks():
    """Return all available workflow block types with their schemas."""
    return {"blocks": get_block_schemas()}


# ── CRUD ─────────────────────────────────────────────────────

@router.post("/workflows", status_code=201, response_model=SavedWorkflowOut)
def save_workflow(req: SaveWorkflowRequest):
    session = SessionLocal()
    try:
        slug = _slugify(req.name)
        # Check for duplicate slug
        existing = session.query(SavedWorkflow).filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{str(existing.id)[:6]}"

        wf = SavedWorkflow(
            name=req.name,
            slug=slug,
            description=req.description,
            graph=req.graph.model_dump(),
        )
        session.add(wf)
        session.commit()
        session.refresh(wf)

        return SavedWorkflowOut(
            id=str(wf.id), name=wf.name, slug=wf.slug,
            description=wf.description,
            block_count=len(req.graph.nodes),
            is_deployed=wf.is_deployed,
            created_at=wf.created_at.isoformat(),
        )
    finally:
        session.close()


@router.get("/workflows/saved", response_model=list[SavedWorkflowOut])
def list_saved_workflows():
    session = SessionLocal()
    try:
        wfs = session.query(SavedWorkflow).order_by(SavedWorkflow.created_at.desc()).all()
        return [
            SavedWorkflowOut(
                id=str(wf.id), name=wf.name, slug=wf.slug,
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
def get_saved_workflow(slug: str):
    session = SessionLocal()
    try:
        wf = session.query(SavedWorkflow).filter_by(slug=slug).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return {
            "id": str(wf.id), "name": wf.name, "slug": wf.slug,
            "description": wf.description, "graph": wf.graph,
            "is_deployed": wf.is_deployed, "created_at": wf.created_at.isoformat(),
        }
    finally:
        session.close()


@router.delete("/workflows/saved/{slug}")
def delete_workflow(slug: str):
    session = SessionLocal()
    try:
        wf = session.query(SavedWorkflow).filter_by(slug=slug).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        session.delete(wf)
        session.commit()
        return {"status": "deleted", "slug": slug}
    finally:
        session.close()


# ── Deploy ───────────────────────────────────────────────────

@router.post("/workflows/saved/{slug}/deploy")
def deploy_workflow(slug: str):
    session = SessionLocal()
    try:
        wf = session.query(SavedWorkflow).filter_by(slug=slug).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
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
async def serve_workflow(slug: str, file: UploadFile = File(...)):
    """Run a deployed workflow with an uploaded image."""
    session = SessionLocal()
    try:
        wf = session.query(SavedWorkflow).filter_by(slug=slug, is_deployed=True).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Deployed workflow not found")
        graph = wf.graph
    finally:
        session.close()

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image")

    def _run():
        return execute_workflow(graph, initial_inputs={"__image__": image})

    result = await asyncio.to_thread(_run)
    return WorkflowRunResponse(**result)


# ── Run (ad-hoc) ─────────────────────────────────────────────

@router.post("/workflows/run", response_model=WorkflowRunResponse)
async def run_workflow_inline(req: WorkflowRunRequest):
    def _run():
        return execute_workflow(req.graph.model_dump())

    result = await asyncio.to_thread(_run)
    return WorkflowRunResponse(**result)


@router.post("/workflows/run/image", response_model=WorkflowRunResponse)
async def run_workflow_with_image(graph: str, file: UploadFile = File(...)):
    import json

    try:
        graph_data = json.loads(graph)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid graph JSON")

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image")

    def _run():
        return execute_workflow(graph_data, initial_inputs={"__image__": image})

    result = await asyncio.to_thread(_run)
    return WorkflowRunResponse(**result)
