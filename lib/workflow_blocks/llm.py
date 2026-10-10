"""LLM block — calls local Ollama for text generation/analysis."""

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from lib.agent.providers import ProviderError, create_chat_model, get_config
from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class LLMBlock(BlockBase):
    name = "llm"
    display_name = "Text LLM"
    description = "Run a text prompt through the workspace-configured provider. Can analyze detection results, generate reports, or make decisions."
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
        model = self.config.get("model") or None
        system_prompt = self.config.get("system_prompt", "You are a helpful computer vision assistant.")

        # Build the full prompt with context
        full_prompt = prompt
        if context is not None:
            full_prompt = f"{prompt}\n\nContext: {context}"

        principal = getattr(self, "execution_principal", None)
        workspace_id = str(principal.workspace_id) if principal else None
        if principal is not None:
            from lib.db import SessionLocal
            from lib.workflow_blocks.platform import _execution_principal

            with SessionLocal() as session:
                _execution_principal(self, session)
        config = getattr(self, "provider_config", None) or get_config(workspace_id)
        try:
            response = create_chat_model(workspace_id=workspace_id, model=model, config=config).invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=full_prompt)]
            )
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("Text provider request failed or timed out; check server configuration") from None
        text = response.content if isinstance(response.content, str) else str(response.content)

        return BlockResult(
            outputs={"response": text},
            metadata={"model": model or config.model, "provider": config.provider, "prompt_length": len(full_prompt)},
        )

    def _config_schema(self) -> dict:
        return {
            "model": {"type": "string", "default": "", "label": "Configured model (blank uses workspace default)"},
            "system_prompt": {
                "type": "text",
                "default": "You are a helpful computer vision assistant.",
                "label": "System prompt",
            },
        }
