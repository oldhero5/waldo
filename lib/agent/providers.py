"""Server-owned text providers. Runtime overrides are workspace-scoped and ephemeral."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from urllib.parse import urlsplit

from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from lib.config import settings

PROVIDERS = frozenset({"ollama", "openai", "anthropic", "vllm", "openrouter"})
CLOUD_PROVIDERS = frozenset({"openai", "anthropic", "openrouter"})


class ProviderError(RuntimeError):
    """Safe public errors never include SDK messages, credentials, or request bodies."""


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    base_url: str = field(repr=False)
    api_key: str = field(default="", repr=False)
    allowed_models: tuple[str, ...] = ()
    allow_cloud_text: bool = False
    timeout: float = 60.0
    source: str = "environment"


_runtime: dict[str, ProviderConfig] = {}
_lock = RLock()


def environment_config() -> ProviderConfig:
    provider = settings.agent_provider
    urls = {
        "ollama": settings.ollama_url,
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "openrouter": "https://openrouter.ai/api/v1",
        "vllm": settings.vllm_url,
    }
    keys = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "openrouter": settings.openrouter_api_key,
        "vllm": settings.vllm_api_key,
    }
    key = keys.get(provider)
    return ProviderConfig(
        provider,
        settings.agent_model,
        urls.get(provider, ""),
        key.get_secret_value() if key else "",
        tuple(m.strip() for m in settings.agent_allowed_models.split(",") if m.strip()),
        settings.agent_allow_cloud_text,
        settings.agent_timeout_seconds,
    )


def get_config(workspace_id: str | None = None) -> ProviderConfig:
    with _lock:
        return _runtime.get(str(workspace_id)) or environment_config()


def validate_config(config: ProviderConfig, model: str | None = None) -> str:
    if config.provider not in PROVIDERS:
        raise ProviderError("Unsupported text provider")
    selected = model or config.model
    if not selected or len(selected) > 200 or any(ord(c) < 32 for c in selected):
        raise ProviderError("A valid model name is required")
    if selected not in {config.model, *config.allowed_models}:
        raise ProviderError("Model is not allowed by the configured provider")
    try:
        parsed = urlsplit(config.base_url)
    except ValueError:
        raise ProviderError("Invalid provider endpoint configuration") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderError("Invalid provider endpoint configuration")
    if not 1 <= config.timeout <= 300:
        raise ProviderError("Provider timeout must be between 1 and 300 seconds")
    if config.provider in CLOUD_PROVIDERS:
        if not config.allow_cloud_text:
            raise ProviderError("Cloud text transfer must be explicitly enabled by an administrator")
        if not config.api_key:
            raise ProviderError("Provider API key is missing")
    return selected


def create_chat_model(
    *, workspace_id: str | None = None, model: str | None = None, config: ProviderConfig | None = None
):
    config = config or get_config(workspace_id)
    selected = validate_config(config, model)
    try:
        common = {"model": selected, "temperature": settings.agent_temperature}
        if config.provider == "ollama":
            return ChatOllama(
                **common,
                base_url=config.base_url,
                num_predict=1024,
                client_kwargs={"timeout": config.timeout, "trust_env": False},
                async_client_kwargs={"timeout": config.timeout, "trust_env": False},
            )
        if config.provider == "anthropic":
            return ChatAnthropic(
                **common,
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.timeout,
                max_retries=0,
                max_tokens=1024,
            )
        return ChatOpenAI(
            **common,
            api_key=config.api_key or "local-vllm",
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=0,
            max_tokens=1024,
            use_responses_api=config.provider == "openai",
        )
    except Exception:
        raise ProviderError("Text provider configuration could not be initialized") from None


def public_status(workspace_id: str | None = None) -> dict:
    config = get_config(workspace_id)
    error = None
    try:
        validate_config(config)
    except ProviderError as exc:
        error = str(exc)
    return {
        "provider": config.provider,
        "model": config.model,
        "ok": error is None,
        "configured": error is None,
        "error": error,
        "source": config.source,
        "cloud_text_enabled": config.allow_cloud_text,
        "models": list(dict.fromkeys((config.model, *config.allowed_models))),
        "connection_verified": config.source == "runtime",
    }


def candidate_config(*, provider: str, model: str, api_key: str, allow_cloud_text: bool) -> ProviderConfig:
    # Browser input cannot supply endpoints. Only operator environment selects local hosts.
    env = environment_config()
    urls = {
        "ollama": settings.ollama_url,
        "vllm": settings.vllm_url,
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    keys = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "openrouter": settings.openrouter_api_key,
        "vllm": settings.vllm_api_key,
    }
    key = keys.get(provider)
    if len(api_key) > 4096:
        raise ProviderError("Invalid provider credential")
    config = ProviderConfig(
        provider,
        model,
        urls.get(provider, ""),
        api_key or (key.get_secret_value() if key else ""),
        (),
        allow_cloud_text,
        env.timeout,
        "runtime",
    )
    validate_config(config)
    return config


def test_connection(config: ProviderConfig) -> None:
    try:
        create_chat_model(config=config).invoke("Reply with OK. This is a connection test.")
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("Text provider connection failed or timed out; check server configuration") from None


def set_runtime_config(workspace_id: str, config: ProviderConfig) -> None:
    validate_config(config)
    with _lock:
        _runtime[str(workspace_id)] = config


def clear_runtime_config(workspace_id: str) -> None:
    with _lock:
        _runtime.pop(str(workspace_id), None)
