"""AI Agent API — proxies Ollama requests to avoid CORS issues."""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from lib.config import settings

router = APIRouter()


@router.post("/agent/chat")
async def agent_chat(request: Request):
    """Proxy chat request to local Ollama with streaming response."""
    body = await request.json()

    # Use the model from request or default
    model = body.get("model", settings.ollama_model)
    messages = body.get("messages", [])

    async def stream():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{settings.ollama_url}/api/chat",
                json={"model": model, "messages": messages, "stream": True},
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        yield line + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.get("/agent/models")
async def list_ollama_models():
    """List available Ollama models."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{settings.ollama_url}/api/tags")
            data = r.json()
            return {
                "models": [
                    {"name": m["name"], "size": m.get("size", 0)}
                    for m in data.get("models", [])
                ]
            }
    except Exception as e:
        return {"models": [], "error": str(e)}
