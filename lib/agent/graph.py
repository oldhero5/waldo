"""LangGraph ReAct agent — Ollama LLM + Waldo tools, with auth-scoped context.

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
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from lib.agent.tools import AgentContext, get_tools, set_context
from lib.config import settings

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
     know whether the user is on CUDA / MPS / CPU.

Today: respond in Markdown. The UI renders it. Avoid emoji unless the user uses one first."""


class AgentState(TypedDict):
    """Graph state — just the running message list."""

    messages: Annotated[list[BaseMessage], add_messages]


def _build_llm(model: str | None, *, allow_actions: bool) -> ChatOllama:
    """Return a tool-bound ChatOllama instance."""
    tools = get_tools(allow_actions=allow_actions)
    llm = ChatOllama(
        model=model or settings.agent_model,
        base_url=settings.ollama_url,
        temperature=settings.agent_temperature,
        # Enough for a multi-tool plan + final answer; bumped from default 256.
        num_predict=1024,
    )
    return llm.bind_tools(tools)


def build_graph(*, model: str | None = None, allow_actions: bool = True):
    """Compile the LangGraph state machine.

    The graph is rebuilt per-call (cheap) so a model override or read-only
    flag from the request takes effect immediately.
    """
    llm = _build_llm(model, allow_actions=allow_actions)
    tool_node = ToolNode(get_tools(allow_actions=allow_actions))

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    def call_model(state: AgentState) -> dict:
        messages = state["messages"]
        # Front-load the system prompt if the caller didn't.
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
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
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        elif role == "tool":
            # Allow rehydrating a prior tool turn (rare but possible).
            out.append(ToolMessage(content=content, tool_call_id=m.get("tool_call_id", "")))
    return out


# ── Sync entry point ──────────────────────────────────────────────
def run_agent(
    messages: list[dict],
    *,
    context: AgentContext,
    model: str | None = None,
) -> dict:
    """Run the agent synchronously. Returns ``{"content": str, "tool_calls": [...]}``."""
    set_context(context)
    graph = build_graph(model=model, allow_actions=context.allow_actions)
    result = graph.invoke({"messages": _coerce_messages(messages)})

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
    graph = build_graph(model=model, allow_actions=context.allow_actions)
    inputs = {"messages": _coerce_messages(messages)}

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
        logger.exception("agent stream failed")
        yield {"type": "error", "message": str(e)}
