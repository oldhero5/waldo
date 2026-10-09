"""Agent and workflow LLMs share a bounded provider factory, tested offline."""

import pytest
from langchain_core.messages import AIMessage

from lib.agent import providers
from lib.workflow_blocks import llm


@pytest.mark.parametrize(
    "config, expected", [({}, "configured-model"), ({"model": "explicit-model"}, "explicit-model")]
)
def test_llm_block_uses_default_or_explicit_model(monkeypatch, config, expected):
    monkeypatch.setattr(providers.settings, "agent_model", "configured-model")
    monkeypatch.setattr(providers.settings, "agent_allowed_models", "explicit-model")
    requests = []

    class Fake:
        def invoke(self, messages):
            requests.append(messages)
            return AIMessage(content="three cars")

    def create(**kwargs):
        providers.validate_config(providers.get_config(kwargs.get("workspace_id")), kwargs.get("model"))
        return Fake()

    monkeypatch.setattr(llm, "create_chat_model", create)
    result = llm.LLMBlock(config).execute({"prompt": "Count cars", "context": {"count": 3}})
    assert result.outputs == {"response": "three cars"}
    assert result.metadata["model"] == expected
    assert requests[0][-1].content == "Count cars\n\nContext: {'count': 3}"


def test_llm_palette_defers_to_workspace_model():
    assert llm.LLMBlock().to_schema()["config_schema"]["model"]["default"] == ""


@pytest.mark.parametrize("failure", [TimeoutError, ValueError])
def test_provider_failure_marks_workflow_node_failed(monkeypatch, failure):
    from lib import workflow_engine

    monkeypatch.setattr(workflow_engine, "BLOCK_REGISTRY", {"llm": llm.LLMBlock})

    class Fake:
        def invoke(self, messages):
            raise failure("credential-secret request-sensitive-data")

    monkeypatch.setattr(llm, "create_chat_model", lambda **kw: Fake())
    result = workflow_engine.execute_workflow(
        {"nodes": [{"id": "analysis", "type": "llm"}], "edges": []}, initial_inputs={"prompt": "Count cars"}
    )
    assert "Node 'analysis' (llm) failed" in result["errors"][0]
    assert "credential-secret" not in str(result)
    assert "analysis" not in result["metadata"]
    assert result["result"] is None


def test_workflow_cannot_override_endpoint_or_unapproved_model(monkeypatch):
    monkeypatch.setattr(providers.settings, "agent_model", "configured-model")
    monkeypatch.setattr(providers.settings, "agent_allowed_models", "")
    with pytest.raises(providers.ProviderError, match="not allowed"):
        llm.LLMBlock({"model": "outside", "base_url": "https://attacker"}).execute({"prompt": "sensitive"})


def test_workflow_keeps_provider_snapshot_across_nodes(monkeypatch):
    from lib import workflow_engine
    from lib.workflow_blocks.base import BlockBase, BlockResult

    seen = []
    first = providers.ProviderConfig("ollama", "first", "http://localhost:11434")
    second = providers.ProviderConfig("ollama", "second", "http://localhost:11434")
    current = [first]
    monkeypatch.setattr(providers, "get_config", lambda _: current[0])

    class SnapshotBlock(BlockBase):
        name = "llm"

        def execute(self, inputs):
            seen.append(self.provider_config)
            current[0] = second
            return BlockResult(outputs={})

    monkeypatch.setattr(workflow_engine, "BLOCK_REGISTRY", {"llm": SnapshotBlock})
    result = workflow_engine.execute_workflow(
        {"nodes": [{"id": "a", "type": "llm"}, {"id": "b", "type": "llm"}], "edges": []}
    )
    assert result["errors"] == []
    assert seen == [first, first]
