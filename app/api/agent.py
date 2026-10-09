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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr

from lib.agent import AgentContext, providers, run_agent, stream_agent
from lib.auth import get_current_user
from lib.authorization import WorkspacePrincipal, get_workspace_principal, require_scope, require_workspace_role

logger = logging.getLogger(__name__)


class RedactedAgentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def redacted(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "Invalid agent request"})

        return redacted


router = APIRouter(dependencies=[Depends(get_current_user)], route_class=RedactedAgentRoute)


# ── Request/response shapes ─────────────────────────────────────────
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=32000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list, max_length=100)
    model: str | None = Field(
        default=None,
        description="Select a model allowed by the server-configured provider.",
    )
    allow_actions: bool = Field(
        default=True,
        description="When false, only read tools are bound. Actions also require editor/admin membership.",
    )


class ChatResponse(BaseModel):
    content: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model: str


# ── Helpers ────────────────────────────────────────────────────────
def _ctx_for(principal: WorkspacePrincipal, *, allow_actions: bool) -> AgentContext:
    require_scope(principal.identity, "read")
    return AgentContext(
        user_id=str(principal.user_id),
        workspace_id=str(principal.workspace_id),
        workspace_role=principal.role,
        identity=principal.identity,
        allow_actions=allow_actions,
    )


def _msg_dicts(req: ChatRequest) -> list[dict]:
    return [m.model_dump(exclude_none=True) for m in req.messages]


# ── Endpoints ──────────────────────────────────────────────────────
@router.get("/agent/health")
async def agent_health(principal: WorkspacePrincipal = Depends(get_workspace_principal)) -> dict:
    """Configuration status only; this does not make a billable model request."""
    require_scope(principal.identity, "read")
    return providers.public_status(str(principal.workspace_id))


@router.get("/agent/models")
async def agent_models(principal: WorkspacePrincipal = Depends(get_workspace_principal)) -> dict:
    require_scope(principal.identity, "read")
    config = providers.get_config(str(principal.workspace_id))
    return {
        "default": config.model,
        "provider": config.provider,
        "cloud_text_enabled": config.allow_cloud_text,
        "models": [
            {"name": name, "size": 0, "backend": config.provider}
            for name in dict.fromkeys((config.model, *config.allowed_models))
        ],
    }


class ProviderRequest(BaseModel):
    model_config = {"extra": "forbid"}
    provider: Literal["ollama", "openai", "anthropic", "vllm", "openrouter"]
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    allow_cloud_text: bool = False


@router.post("/agent/provider")
async def configure_provider(
    req: ProviderRequest, principal: WorkspacePrincipal = Depends(get_workspace_principal)
) -> dict:
    """Admin-only, write-only runtime override. Tests a short text request before applying."""
    require_workspace_role(principal, "admin")
    import asyncio

    try:
        config = providers.candidate_config(
            provider=req.provider,
            model=req.model,
            api_key=req.api_key.get_secret_value(),
            allow_cloud_text=req.allow_cloud_text,
        )
        await asyncio.to_thread(providers.test_connection, config)
        # The connection test can outlive a membership or API-key grant.
        from lib import authorization

        with authorization.SessionLocal() as session:
            live = authorization.resolve_workspace(session, principal.identity, principal.workspace_id)
            require_workspace_role(live, "admin")
        providers.set_runtime_config(str(principal.workspace_id), config)
    except providers.ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {**providers.public_status(str(principal.workspace_id)), "connection_verified": True}


@router.delete("/agent/provider")
async def reset_provider(principal: WorkspacePrincipal = Depends(get_workspace_principal)) -> dict:
    require_workspace_role(principal, "admin")
    providers.clear_runtime_config(str(principal.workspace_id))
    return providers.public_status(str(principal.workspace_id))


@router.post("/agent/chat", response_model=ChatResponse)
async def agent_chat(
    req: ChatRequest, principal: WorkspacePrincipal = Depends(get_workspace_principal)
) -> ChatResponse:
    """Run the agent and return the final answer + tool-call summary.

    Use ``/agent/stream`` for a token-by-token SSE feed.
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must be non-empty")

    ctx = _ctx_for(principal, allow_actions=req.allow_actions)

    provider_config = providers.get_config(ctx.workspace_id)
    try:
        providers.validate_config(provider_config, req.model)
    except providers.ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # The agent's invoke call is sync (langchain's blocking path) — push it
    # off the event loop so we don't block the worker thread.
    import asyncio  # noqa: PLC0415

    def _run() -> dict:
        return run_agent(_msg_dicts(req), context=ctx, model=req.model, provider_config=provider_config)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_chat failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Text provider request failed; check server configuration") from e

    return ChatResponse(
        content=result["content"],
        tool_calls=result["tool_calls"],
        model=req.model or provider_config.model,
    )


@router.post("/agent/stream")
async def agent_stream(
    req: ChatRequest, principal: WorkspacePrincipal = Depends(get_workspace_principal)
) -> StreamingResponse:
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

    ctx = _ctx_for(principal, allow_actions=req.allow_actions)

    provider_config = providers.get_config(ctx.workspace_id)
    try:
        providers.validate_config(provider_config, req.model)
    except providers.ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    async def event_source():
        try:
            async for event in stream_agent(
                _msg_dicts(req), context=ctx, model=req.model, provider_config=provider_config
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_stream failed (%s)", type(e).__name__)
            yield f"data: {json.dumps({'type': 'error', 'message': 'Text provider request failed; check server configuration'})}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable buffering on nginx-style proxies
        },
    )
