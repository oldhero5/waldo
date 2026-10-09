"""LangGraph ReAct agent — configured text provider + Waldo tools, with auth-scoped context.

The graph is a textbook two-node ReAct loop:

    START -> agent -> tools -> agent -> ... -> END

``agent`` is the LLM call; ``tools`` runs whatever the LLM asked for. The
``should_continue`` edge stops the loop when the LLM returns an answer with
no further tool calls.

Why so small? Because the loop *should* be small. Anything fancier (planner,
reflection, retrievers) belongs in tools, not in the graph topology.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from lib.agent.providers import ProviderConfig, create_chat_model, get_config
from lib.agent.tools import AgentContext, _ctx_or_raise, get_tools, set_context

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are Waldo, the in-app AI assistant for a self-hosted computer-vision
platform. The user is signed in to a workspace; tools you call are automatically
scoped to that workspace, so you never need a workspace_id argument.

You help with:
  • Inspecting projects, videos, datasets, models, and training runs.
  • Starting labeling jobs (SAM 3) and YOLO training runs on the user's behalf.
  • Activating a trained model so /predict/* serves it.
  • Recommending hyperparameters and explaining metrics (mAP, precision, recall).

Operating rules:
  1. Use tools to get facts. Never invent IDs, mAP numbers, or counts.
  2. Before calling an action tool (start_labeling_job, start_training, activate_model)
     state in one short sentence what you're about to do, then call it. After it
     returns, summarize the result and include the UI URL the tool emitted.
  3. If a tool errors, surface the error verbatim — do not fabricate success.
  4. Keep prose short. Bulleted lists for comparisons, fenced code only when
     showing real commands.
  5. When asked for hardware-aware advice, call get_system_info first so you
     can report its scope accurately. API-process CPU/GPU availability does
     not establish worker or text-provider hardware. If worker hardware is
     not reported, say it is unknown; do not infer that MLX/MPS is unavailable.

Today: respond in Markdown. The UI renders it. Avoid emoji unless the user uses one first."""


class AgentState(TypedDict):
    """Graph state — just the running message list."""

    messages: Annotated[list[BaseMessage], add_messages]


def _build_llm(model: str | None, *, allow_actions: bool, provider_config: ProviderConfig | None = None):
    context = _ctx_or_raise()
    llm = create_chat_model(workspace_id=context.workspace_id, model=model, config=provider_config)
    return llm.bind_tools(get_tools(allow_actions=allow_actions))


def build_graph(*, model: str | None = None, allow_actions: bool = True, provider_config: ProviderConfig | None = None):
    """Compile the LangGraph state machine.

    The graph is rebuilt per-call (cheap) so a model override or read-only
    flag from the request takes effect immediately.
    """
    llm = _build_llm(model, allow_actions=allow_actions, provider_config=provider_config)
    tool_node = ToolNode(get_tools(allow_actions=allow_actions))

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    def call_model(state: AgentState) -> dict:
        # Client history can never replace the server-owned policy.
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + [
            message for message in state["messages"] if not isinstance(message, SystemMessage)
        ]
        response = llm.invoke(messages)
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


# ── Conversion helpers ─────────────────────────────────────────────
def _coerce_messages(raw: list[dict]) -> list[BaseMessage]:
    """Turn the wire-format chat history into LangChain messages."""
    out: list[BaseMessage] = []
    for m in raw:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        else:
            raise ValueError("Chat history role must be user or assistant")
    return out


# ── Sync entry point ──────────────────────────────────────────────
def run_agent(
    messages: list[dict],
    *,
    context: AgentContext,
    model: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> dict:
    """Run the agent synchronously. Returns ``{"content": str, "tool_calls": [...]}``."""
    set_context(context)
    _ctx_or_raise()
    inputs = {"messages": _coerce_messages(messages)}
    graph = build_graph(
        model=model,
        allow_actions=context.actions_allowed,
        provider_config=provider_config or get_config(context.workspace_id),
    )
    result = graph.invoke(inputs)

    final_text = ""
    tool_calls: list[dict] = []
    for msg in result["messages"]:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append({"name": tc["name"], "args": tc.get("args", {})})
            if msg.content:
                final_text = msg.content if isinstance(msg.content, str) else str(msg.content)

    return {"content": final_text or "(no response)", "tool_calls": tool_calls}


# ── Streaming entry point ─────────────────────────────────────────
async def stream_agent(
    messages: list[dict],
    *,
    context: AgentContext,
    model: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> AsyncIterator[dict]:
    """Stream agent events as a sequence of small dicts.

    Event shapes (all JSON-serializable):

      {"type": "token",      "content": "partial text"}
      {"type": "tool_call",  "name": "list_models", "args": {...}}
      {"type": "tool_result","name": "list_models", "content": "..."}
      {"type": "done"}
      {"type": "error",      "message": "..."}
    """
    set_context(context)
    _ctx_or_raise()
    inputs = {"messages": _coerce_messages(messages)}
    graph = build_graph(
        model=model,
        allow_actions=context.actions_allowed,
        provider_config=provider_config or get_config(context.workspace_id),
    )

    try:
        async for kind, payload in graph.astream(inputs, stream_mode=["messages", "updates"]):
            if kind == "messages":
                # payload = (message_chunk, metadata)
                chunk, meta = payload
                node = meta.get("langgraph_node") if isinstance(meta, dict) else None
                if node != "agent":
                    continue
                if isinstance(chunk, AIMessageChunk):
                    if chunk.content:
                        text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                        yield {"type": "token", "content": text}
                    # Tool calls don't always arrive in `content`; surface them too.
                    for tc in chunk.tool_calls or []:
                        if tc.get("name"):
                            yield {"type": "tool_call", "name": tc["name"], "args": tc.get("args") or {}}
            elif kind == "updates":
                # payload = {node_name: {"messages": [...]}, ...}
                if not isinstance(payload, dict):
                    continue
                tools_update = payload.get("tools")
                if not tools_update:
                    continue
                for msg in tools_update.get("messages", []):
                    if isinstance(msg, ToolMessage):
                        yield {
                            "type": "tool_result",
                            "name": msg.name or "tool",
                            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                        }
        yield {"type": "done"}
    except Exception as e:  # noqa: BLE001
        logger.warning("agent stream failed (%s)", type(e).__name__)
        yield {"type": "error", "message": "Text provider request failed; check server configuration"}
