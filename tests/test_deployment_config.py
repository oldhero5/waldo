"""Compose should isolate infrastructure and allow hosted-only chat."""

from pathlib import Path

import yaml


def services():
    return yaml.safe_load((Path(__file__).parents[1] / "docker-compose.yml").read_text())["services"]


def test_infrastructure_ports_default_to_loopback():
    for name in ("postgres", "redis", "ollama", "minio"):
        assert all("127.0.0.1" in port for port in services()[name]["ports"])


def test_local_chat_is_optional_and_has_no_application_startup_dependency():
    config = services()
    assert config["ollama"]["profiles"] == ["local-chat"]
    assert config["ollama-init"]["profiles"] == ["local-chat"]
    assert "ollama" not in config["waldo-app"]["depends_on"]
    assert "WALDO_AGENT_MODEL" not in config["ollama-init"]["entrypoint"]


def test_services_use_the_application_credential_variables():
    config = services()
    assert "POSTGRES_PASSWORD" in config["postgres"]["environment"]["POSTGRES_PASSWORD"]
    assert "MINIO_SECRET_KEY" in config["minio"]["environment"]["MINIO_ROOT_PASSWORD"]
    assert "MINIO_SECRET_KEY" in config["minio-init"]["environment"]
    assert "set -eu" in config["minio-init"]["entrypoint"]
