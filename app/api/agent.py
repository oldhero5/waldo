"""AI Agent API — Gemma4 via mlx-vlm (native Apple Silicon) with Ollama fallback."""

import asyncio
import json

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from lib.auth import get_current_user
from lib.config import settings

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.post("/agent/chat")
async def agent_chat(request: Request):
    """Chat with the Waldo AI agent.

    Uses Gemma4 via mlx-vlm by default (native Apple Silicon).
    Falls back to Ollama if mlx-vlm is unavailable.
    Set use_tools=true for tool-augmented responses.
    """
    body = await request.json()
    messages = body.get("messages", [])
    use_tools = body.get("use_tools", True)
    backend = body.get("backend", "mlx")  # "mlx" or "ollama"

    if backend == "mlx":

        def _run():
            from lib.agent.mlx_agent import generate_response

            return generate_response(messages)

        try:
            response_text = await asyncio.to_thread(_run)
            result = json.dumps({"message": {"role": "assistant", "content": response_text}, "done": True})
            return StreamingResponse(iter([result + "\n"]), media_type="application/x-ndjson")
        except Exception as e:
            # Fall back to Ollama if mlx-vlm fails
            error = json.dumps({"message": {"role": "assistant", "content": f"MLX agent error: {e}"}, "done": True})
            return StreamingResponse(iter([error + "\n"]), media_type="application/x-ndjson")

    elif use_tools:
        # LangGraph agent with Ollama
        def _run():
            from lib.agent.graph import run_agent

            model = body.get("model", settings.ollama_model)
            return run_agent(messages, model_name=model)

        try:
            response_text = await asyncio.to_thread(_run)
            result = json.dumps({"message": {"role": "assistant", "content": response_text}, "done": True})
            return StreamingResponse(iter([result + "\n"]), media_type="application/x-ndjson")
        except Exception as e:
            error = json.dumps({"message": {"role": "assistant", "content": f"Agent error: {e}"}, "done": True})
            return StreamingResponse(iter([error + "\n"]), media_type="application/x-ndjson")
    else:
        # Direct Ollama streaming
        model = body.get("model", settings.ollama_model)

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


@router.post("/agent/insights")
async def agent_insights(request: Request):
    """Generate contextual greeting + suggestions from workspace state. Non-blocking."""
    body = await request.json()

    prompt = f"""You are Waldo, an AI assistant for a computer vision platform.
Given the user's workspace state below, respond with ONLY valid JSON (no markdown, no backticks):
{{"greeting": "A short witty one-liner greeting (max 15 words, CV/ML themed, nerdy but warm)",
"suggestions": ["actionable suggestion 1", "actionable suggestion 2", "actionable suggestion 3"]}}

Workspace state:
- Videos uploaded: {body.get("videos", 0)}
- Annotations created: {body.get("annotations", 0)}
- Datasets completed: {body.get("datasets", 0)}
- Models trained: {body.get("models", 0)}
- Best mAP50: {body.get("best_map", "—")}
- Active training: {body.get("training", False)}
- Model deployed: {body.get("deployed", False)}

Rules for suggestions:
- Be specific to their state (don't suggest uploading if they have 1000+ annotations)
- Focus on the next highest-impact action
- Keep each suggestion under 12 words
- If they have a deployed model with good mAP, suggest monitoring or A/B testing
- If mAP is below 70%, suggest data improvements

Rules for greeting:
- Reference something specific about their state
- Be witty and concise, like a senior ML engineer would say
- No emojis"""

    def _run():
        try:
            from mlx_vlm import generate
            from mlx_vlm.prompt_utils import apply_chat_template

            from lib.agent.mlx_agent import _get_model

            model, processor = _get_model()
            formatted = apply_chat_template(processor, model.config, prompt)
            result = generate(model=model, processor=processor, prompt=formatted, max_tokens=200, temperature=0.8)
            text = result.text.strip()
            if "{" in text and "}" in text:
                json_str = text[text.index("{") : text.rindex("}") + 1]
                return json.loads(json_str)
        except Exception:
            import traceback

            traceback.print_exc()
        return None

    try:
        # Run with a timeout to prevent hanging
        result = await asyncio.wait_for(asyncio.to_thread(_run), timeout=30.0)
        if result and "greeting" in result:
            return result
    except (TimeoutError, Exception):
        pass

    return {"greeting": None, "suggestions": []}


@router.get("/agent/models")
async def list_agent_models():
    """List available models (mlx-vlm + Ollama)."""
    models = [
        {"name": settings.agent_model_id, "backend": "mlx", "size": 0},
    ]

    # Also list Ollama models if available
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_url}/api/tags")
            data = r.json()
            for m in data.get("models", []):
                models.append({"name": m["name"], "backend": "ollama", "size": m.get("size", 0)})
    except Exception:
        pass

    return {"models": models}
