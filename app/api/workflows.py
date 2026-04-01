"""Workflow API — create, run, and deploy visual ML pipelines."""
import asyncio

import cv2
import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

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


@router.get("/workflows/blocks")
def list_blocks():
    """Return all available workflow block types with their schemas."""
    return {"blocks": get_block_schemas()}


@router.post("/workflows/run", response_model=WorkflowRunResponse)
async def run_workflow_inline(req: WorkflowRunRequest):
    """Execute a workflow graph with no input (for testing)."""
    def _run():
        return execute_workflow(req.graph.model_dump())

    result = await asyncio.to_thread(_run)
    return WorkflowRunResponse(**result)


@router.post("/workflows/run/image", response_model=WorkflowRunResponse)
async def run_workflow_with_image(
    graph: str,  # JSON string of WorkflowGraph
    file: UploadFile = File(...),
):
    """Execute a workflow with an image input."""
    import json

    try:
        graph_data = json.loads(graph)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid graph JSON")

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image file")

    def _run():
        return execute_workflow(graph_data, initial_inputs={"__image__": image})

    result = await asyncio.to_thread(_run)
    return WorkflowRunResponse(**result)
