from pydantic_settings import BaseSettings

INSECURE_DEV_DEFAULTS = {
    "postgres_password": {"waldo", "postgres", ""},
    "minio_access_key": {"minioadmin", ""},
    "minio_secret_key": {"minioadmin", ""},
    "jwt_secret": {"waldo-dev-secret-change-in-production", ""},
}


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    # Deployment environment — production enforces secret hardening
    app_env: str = "development"

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "waldo"
    postgres_password: str = "waldo"
    postgres_db: str = "waldo"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_external_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "waldo"
    minio_secure: bool = False

    # Hugging Face
    hf_token: str = ""

    # SAM 3 (PyTorch/transformers) + SAM 3.1 (MLX)
    sam3_model_id: str = "facebook/sam3"
    sam3_mlx_model_id: str = "mlx-community/sam3.1-bf16"

    # Auth — jwt_secret MUST be overridden in production (validated at startup)
    jwt_secret: str = "waldo-dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # AI Agent (Gemma4 via mlx-vlm on Apple Silicon)
    agent_model_id: str = "google/gemma-4-e4b-it"
    # Ollama fallback
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:12b"

    # Device
    device: str = "mps"
    dtype: str = "float32"

    # Notifications (all optional)
    slack_webhook_url: str = ""
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    alert_email: str = ""

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    def validate_production_secrets(self) -> list[str]:
        """Return list of insecure-default fields. Empty list = safe."""
        problems: list[str] = []
        for field, bad_values in INSECURE_DEV_DEFAULTS.items():
            if getattr(self, field) in bad_values:
                problems.append(field)
        return problems


settings = Settings()


def enforce_production_secrets() -> None:
    """Call at app startup. Raises RuntimeError in production if secrets are weak."""
    if not settings.is_production():
        return
    problems = settings.validate_production_secrets()
    if problems:
        raise RuntimeError(
            "Refusing to start in production with insecure defaults for: "
            + ", ".join(problems)
            + ". Set these via environment variables."
        )
