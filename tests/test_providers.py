"""Provider selection and authority without network calls or paid generations."""

from dataclasses import replace

import pytest

from lib.agent import providers


@pytest.fixture(autouse=True)
def reset_runtime():
    providers._runtime.clear()
    yield
    providers._runtime.clear()


def configured(name):
    urls = {
        "ollama": "http://localhost:11434",
        "vllm": "http://localhost:8001/v1",
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    return providers.ProviderConfig(name, "chosen-model", urls[name], "private-credential", ("allowed-model",), True, 7)


@pytest.mark.parametrize(
    "name,adapter",
    [
        ("ollama", "ChatOllama"),
        ("openai", "ChatOpenAI"),
        ("anthropic", "ChatAnthropic"),
        ("vllm", "ChatOpenAI"),
        ("openrouter", "ChatOpenAI"),
    ],
)
def test_factory_uses_maintained_adapter(monkeypatch, name, adapter):
    seen = []
    marker = object()
    for candidate in ("ChatOllama", "ChatAnthropic", "ChatOpenAI"):

        def build(_name=candidate, **kwargs):
            seen.append((_name, kwargs))
            return marker

        monkeypatch.setattr(providers, candidate, build)
    assert providers.create_chat_model(config=configured(name), model="allowed-model") is marker
    assert seen[0][0] == adapter
    assert seen[0][1]["model"] == "allowed-model"
    assert seen[0][1]["base_url"] == configured(name).base_url
    if name == "ollama":
        assert seen[0][1]["client_kwargs"]["timeout"] == 7
    else:
        assert seen[0][1]["timeout"] == 7
        assert seen[0][1]["max_retries"] == 0
        if name != "anthropic":
            assert seen[0][1]["use_responses_api"] is (name == "openai")


@pytest.mark.parametrize(
    "change, error",
    [
        ({"api_key": ""}, "key is missing"),
        ({"allow_cloud_text": False}, "explicitly enabled"),
        ({"provider": "unknown"}, "Unsupported"),
        ({"base_url": "https://user:password@host"}, "endpoint"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_bad_config_does_not_initialize_provider(monkeypatch, change, error):
    monkeypatch.setattr(providers, "ChatOpenAI", lambda **kw: pytest.fail("must reject before initialization"))
    with pytest.raises(providers.ProviderError, match=error):
        providers.create_chat_model(config=replace(configured("openai"), **change))


def test_model_override_is_bounded():
    with pytest.raises(providers.ProviderError, match="not allowed"):
        providers.create_chat_model(config=configured("openrouter"), model="unapproved-provider/model")


def test_status_and_repr_never_expose_credentials():
    config = configured("openai")
    providers.set_runtime_config("one", config)
    assert "private-credential" not in repr(config)
    assert "private-credential" not in str(providers.public_status("one"))
    assert "api_key" not in providers.public_status("one")
    assert providers.get_config("other").source == "environment"
    providers.clear_runtime_config("one")
    assert providers.get_config("one").source == "environment"


@pytest.mark.parametrize("failure", [TimeoutError, ValueError])
def test_connection_failure_redacts_sdk_error(monkeypatch, failure):
    class Fake:
        def invoke(self, prompt):
            raise failure("private-credential provider request body sensitive text")

    monkeypatch.setattr(providers, "create_chat_model", lambda **kw: Fake())
    with pytest.raises(providers.ProviderError) as exc:
        providers.test_connection(configured("openai"))
    assert "private-credential" not in str(exc.value)
    assert "sensitive text" not in str(exc.value)


@pytest.mark.parametrize("name", ["ollama", "openai", "anthropic", "vllm", "openrouter"])
def test_real_adapter_initializes_offline(name):
    # Constructor only: validates maintained adapters' actual option names, no request.
    model = providers.create_chat_model(config=configured(name))
    assert model is not None
