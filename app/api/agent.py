"""HTTP surface for the Waldo agent.

Endpoints:

    GET  /api/v1/agent/health   — backend reachable + model present?
    GET  /api/v1/agent/models   — list models the local Ollama can serve
    POST /api/v1/agent/chat     — send messages, get a JSON response
    POST /api/v1/agent/stream   — same input, Server-Sent Events stream

Auth: every endpoint requires a signed-in user. The agent runs inside an
:class:`~lib.agent.tools.AgentContext` derived from that user, so tool calls
are pinned to their workspace.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lib.agent import AgentContext, run_agent, stream_agent
from lib.auth import get_current_user
from lib.config import settings
from lib.db import SessionLocal, User, WorkspaceMember

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


# ── Request/response shapes ─────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str = Field(..., description="user | assistant | system | tool")
    content: str
    tool_call_id: str | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    model: str | None = Field(
        default=None,
        description="Override the configured agent model (must be available locally).",
    )
    allow_actions: bool = Field(
        default=True,
        description="When false, only read tools are bound — agent cannot start jobs.",
    )


class ChatResponse(BaseModel):
    content: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model: str


# ── Helpers ────────────────────────────────────────────────────────
def _resolve_workspace_id(user: User) -> str | None:
    """Return the user's primary workspace id, if any."""
    session = SessionLocal()
    try:
        member = session.query(WorkspaceMember).filter_by(user_id=user.id).first()
        return str(member.workspace_id) if member else None
    finally:
        session.close()


def _ctx_for(user: User, *, allow_actions: bool) -> AgentContext:
    return AgentContext(
        user_id=str(user.id),
        workspace_id=_resolve_workspace_id(user),
        allow_actions=allow_actions,
    )


def _msg_dicts(req: ChatRequest) -> list[dict]:
    return [m.model_dump(exclude_none=True) for m in req.messages]


# ── Endpoints ──────────────────────────────────────────────────────
@router.get("/agent/health")
async def agent_health() -> dict:
    """Quick reachability check — proves Ollama is up and the model is present."""
    out: dict[str, Any] = {"ollama_url": settings.ollama_url, "model": settings.agent_model}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_url}/api/tags")
            r.raise_for_status()
            data = r.json()
            tags = [m.get("name") for m in data.get("models", [])]
            out["ok"] = True
            out["model_present"] = settings.agent_model in tags
            out["available_models"] = tags
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["error"] = str(e)
    return out


@router.get("/agent/models")
async def agent_models() -> dict:
    """List models the local Ollama can serve."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_url}/api/tags")
            r.raise_for_status()
            data = r.json()
            return {
                "default": settings.agent_model,
                "models": [
                    {"name": m["name"], "size": m.get("size", 0), "backend": "ollama"} for m in data.get("models", [])
                ],
            }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Ollama unreachable: {e}") from e


@router.post("/agent/chat", response_model=ChatResponse)
async def agent_chat(req: ChatRequest, user: User = Depends(get_current_user)) -> ChatResponse:
    """Run the agent and return the final answer + tool-call summary.

    Use ``/agent/stream`` for a token-by-token SSE feed.
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must be non-empty")

    ctx = _ctx_for(user, allow_actions=req.allow_actions)

    # The agent's invoke call is sync (langchain's blocking path) — push it
    # off the event loop so we don't block the worker thread.
    import asyncio  # noqa: PLC0415

    def _run() -> dict:
        return run_agent(_msg_dicts(req), context=ctx, model=req.model)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as e:  # noqa: BLE001
        logger.exception("agent_chat failed")
        raise HTTPException(status_code=500, detail=f"agent error: {e}") from e

    return ChatResponse(
        content=result["content"],
        tool_calls=result["tool_calls"],
        model=req.model or settings.agent_model,
    )


@router.post("/agent/stream")
async def agent_stream(req: ChatRequest, user: User = Depends(get_current_user)) -> StreamingResponse:
    """Stream agent output as Server-Sent Events.

    Each SSE message is JSON:
        {"type": "token",       "content": "..."}
        {"type": "tool_call",   "name": "list_models", "args": {...}}
        {"type": "tool_result", "name": "list_models", "content": "..."}
        {"type": "done"}
        {"type": "error",       "message": "..."}
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must be non-empty")

    ctx = _ctx_for(user, allow_actions=req.allow_actions)

    async def event_source():
        try:
            async for event in stream_agent(_msg_dicts(req), context=ctx, model=req.model):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.exception("agent_stream failed mid-stream")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable buffering on nginx-style proxies
        },
    )
