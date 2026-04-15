"""Gemma4 agent via mlx-vlm — native Apple Silicon, no Ollama dependency.

Uses a simple ReAct loop: generate → check for tool calls → execute → continue.
Gemma4-IT models support function calling through the chat template.
"""

import json
import logging

from lib.agent.tools import WALDO_TOOLS
from lib.config import settings

logger = logging.getLogger(__name__)

_model = None
_processor = None

SYSTEM_PROMPT = """You are Waldo, an expert AI assistant for a computer vision platform.

You have access to tools that can query the Waldo platform. When users ask about their data, use the tools to get real information.

Available tools:
{tool_descriptions}

To use a tool, respond with a JSON block:
```tool
{{"name": "tool_name", "arguments": {{"arg1": "value1"}}}}
```

After receiving tool results, provide a natural language answer to the user.
Be concise and practical. Use specific numbers when suggesting configurations."""


def _get_model():
    """Load Gemma4 model once and cache."""
    global _model, _processor
    if _model is None:
        from mlx_vlm import load

        logger.info("Loading Gemma4 agent model: %s", settings.agent_model_id)
        _model, _processor = load(settings.agent_model_id)
        logger.info("Gemma4 agent model loaded")
    return _model, _processor


def _build_tool_descriptions() -> str:
    """Format tool descriptions for the system prompt."""
    lines = []
    for tool in WALDO_TOOLS:
        name = tool.name
        desc = tool.description
        # Get args from the tool's schema
        schema = tool.args_schema.schema() if hasattr(tool, "args_schema") and tool.args_schema else {}
        props = schema.get("properties", {})
        args_desc = ", ".join(f"{k}: {v.get('description', v.get('type', 'any'))}" for k, v in props.items())
        lines.append(f"- {name}({args_desc}): {desc}")
    return "\n".join(lines)


def _extract_tool_call(text: str) -> dict | None:
    """Extract a tool call JSON from the response text."""
    # Look for ```tool ... ``` blocks
    if "```tool" in text:
        start = text.index("```tool") + len("```tool")
        end = text.index("```", start) if "```" in text[start:] else len(text)
        try:
            return json.loads(text[start : start + end].strip())
        except json.JSONDecodeError:
            pass

    # Look for raw JSON with "name" and "arguments"
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("{") and '"name"' in line:
            try:
                parsed = json.loads(line)
                if "name" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass

    return None


def _execute_tool(tool_call: dict) -> str:
    """Execute a tool and return the result as a string."""
    name = tool_call.get("name", "")
    args = tool_call.get("arguments", {})

    for tool in WALDO_TOOLS:
        if tool.name == name:
            try:
                result = tool.invoke(args)
                return json.dumps(result, default=str) if not isinstance(result, str) else result
            except Exception as e:
                return f"Tool error: {e}"

    return f"Unknown tool: {name}"


def generate_response(messages: list[dict], max_tool_rounds: int = 3) -> str:
    """Run the Gemma4 agent with tool calling support.

    Args:
        messages: Chat history as [{"role": "user/assistant/system", "content": "..."}]
        max_tool_rounds: Max tool call iterations before returning

    Returns:
        Final assistant response text
    """
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template

    model, processor = _get_model()

    # Build system prompt with tool descriptions
    tool_desc = _build_tool_descriptions()
    system = SYSTEM_PROMPT.format(tool_descriptions=tool_desc)

    # Build conversation history
    history = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] in ("user", "assistant"):
            history.append(m)

    for _round in range(max_tool_rounds + 1):
        # Format prompt
        prompt_text = apply_chat_template(
            processor,
            model.config,
            history[-1]["content"] if history else "",
        )

        # Generate
        gen_result = generate(
            model=model,
            processor=processor,
            prompt=prompt_text,
            max_tokens=1024,
            temperature=0.7,
            top_p=0.95,
        )
        text = gen_result.text

        # Check for tool calls
        tool_call = _extract_tool_call(text)
        if tool_call and _round < max_tool_rounds:
            logger.info("Agent tool call: %s", tool_call.get("name"))
            tool_result = _execute_tool(tool_call)

            # Add assistant response + tool result to history
            history.append({"role": "assistant", "content": text})
            history.append(
                {
                    "role": "user",
                    "content": f"Tool result for {tool_call['name']}:\n{tool_result}\n\nNow provide your answer to the user based on this information.",
                }
            )
            continue

        # No tool call — return the response
        return text

    return text
