"""Waldo AI Agent — LangGraph ReAct loop served by a local Ollama LLM.

Public surface:

    from lib.agent import AgentContext, run_agent, stream_agent

The graph itself lives in :mod:`lib.agent.graph`, the tools (read + action,
auth-scoped) in :mod:`lib.agent.tools`. The HTTP layer is :mod:`app.api.agent`.
"""

from lib.agent.graph import build_graph, run_agent, stream_agent
from lib.agent.tools import AgentContext, get_tools, set_context

__all__ = [
    "AgentContext",
    "build_graph",
    "get_tools",
    "run_agent",
    "set_context",
    "stream_agent",
]
