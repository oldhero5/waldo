"""LangGraph agent — ReAct loop with Waldo tools, powered by local Ollama."""
import logging
from typing import Annotated

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from lib.agent.tools import WALDO_TOOLS
from lib.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Waldo, an expert AI assistant for a computer vision platform.

You have access to tools that can query the Waldo platform:
- list_projects: See all video collections
- list_datasets: See labeled datasets with annotation counts
- list_models: See trained models with accuracy metrics
- list_experiments: See training run history
- get_training_tips: Get recommended hyperparameters

When users ask about their data, USE THE TOOLS to get real information instead of guessing.
When suggesting configurations, be specific with numbers (epochs, batch size, etc.).
When explaining concepts, be concise and practical.

You help with:
- Object detection and segmentation (YOLO + SAM3)
- Training configuration and augmentation strategies
- Building ML workflows (visual pipelines)
- Understanding metrics (mAP, precision, recall)
- Debugging model performance issues
- Deployment best practices"""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def create_agent(model_name: str | None = None):
    """Create a LangGraph agent with Waldo tools."""
    model = model_name or settings.ollama_model

    llm = ChatOllama(
        model=model,
        base_url=settings.ollama_url,
        temperature=0.7,
    ).bind_tools(WALDO_TOOLS)

    tool_node = ToolNode(WALDO_TOOLS)

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    def call_model(state: AgentState) -> dict:
        messages = state["messages"]
        # Ensure system prompt is first
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
        response = llm.invoke(messages)
        return {"messages": [response]}

    # Build graph
    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


def run_agent(messages: list[dict], model_name: str | None = None) -> str:
    """Run the agent synchronously and return the final response text."""
    agent = create_agent(model_name)

    # Convert dict messages to LangChain message objects
    lc_messages = []
    for m in messages:
        if m["role"] == "system":
            lc_messages.append(SystemMessage(content=m["content"]))
        elif m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_messages.append(AIMessage(content=m["content"]))

    result = agent.invoke({"messages": lc_messages})

    # Get the final assistant message
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content

    return "I couldn't generate a response."
