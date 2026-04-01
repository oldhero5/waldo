"""LLM block — calls local Ollama for text generation/analysis."""
from typing import Any

import httpx

from lib.config import settings
from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class LLMBlock(BlockBase):
    name = "llm"
    display_name = "LLM (Ollama)"
    description = "Run a text prompt through a local LLM via Ollama. Can analyze detection results, generate reports, or make decisions."
    category = "ai"
    input_ports = [
        Port("prompt", "text", "Text prompt or template"),
        Port("context", "any", "Additional context (detections, counts, etc.)", required=False),
    ]
    output_ports = [
        Port("response", "text", "LLM response text"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        prompt = inputs.get("prompt", "")
        context = inputs.get("context", None)
        model = self.config.get("model", settings.ollama_model)
        system_prompt = self.config.get("system_prompt", "You are a helpful computer vision assistant.")

        # Build the full prompt with context
        full_prompt = prompt
        if context is not None:
            full_prompt = f"{prompt}\n\nContext: {context}"

        # Call Ollama
        try:
            response = httpx.post(
                f"{settings.ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "system": system_prompt,
                    "stream": False,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            result = response.json()
            text = result.get("response", "")
        except Exception as e:
            text = f"LLM error: {e}"

        return BlockResult(
            outputs={"response": text},
            metadata={"model": model, "prompt_length": len(full_prompt)},
        )

    def _config_schema(self) -> dict:
        return {
            "model": {"type": "string", "default": "llama3.2", "label": "Ollama model"},
            "system_prompt": {"type": "text", "default": "You are a helpful computer vision assistant.", "label": "System prompt"},
        }
