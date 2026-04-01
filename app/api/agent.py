"""AI Agent API — LangGraph agent with Waldo tools + Ollama streaming proxy."""
import asyncio

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from lib.config import settings

router = APIRouter()


class AgentRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    use_tools: bool = True  # If true, uses LangGraph agent with tools


@router.post("/agent/chat")
async def agent_chat(request: Request):
    """Chat with the Waldo AI agent.

    If use_tools=true (default), runs through LangGraph with Waldo tools.
    Otherwise, streams directly from Ollama for faster simple Q&A.
    """
    body = await request.json()
    model = body.get("model", settings.ollama_model)
    messages = body.get("messages", [])
    use_tools = body.get("use_tools", True)

    if use_tools:
        # Use LangGraph agent (synchronous, returns full response)
        def _run():
            from lib.agent.graph import run_agent
            return run_agent(messages, model_name=model)

        try:
            response_text = await asyncio.to_thread(_run)
            # Return as a streaming-compatible format (single chunk)
            import json
            result = json.dumps({"message": {"role": "assistant", "content": response_text}, "done": True})
            return StreamingResponse(iter([result + "\n"]), media_type="application/x-ndjson")
        except Exception as e:
            import json
            error = json.dumps({"message": {"role": "assistant", "content": f"Agent error: {e}"}, "done": True})
            return StreamingResponse(iter([error + "\n"]), media_type="application/x-ndjson")
    else:
        # Direct Ollama streaming (no tools, faster for simple Q&A)
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
