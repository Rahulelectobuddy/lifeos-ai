from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://lifeos_app:change-me@db.invalid/lifeos"
    )
    migration_database_url: SecretStr | None = None
    api_token: SecretStr = SecretStr("")
    zulip_url: str = "https://zulip.invalid"
    zulip_bot_email: str = ""
    zulip_api_key: SecretStr = SecretStr("")
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_stream_ids: list[int] = Field(default_factory=list)
    dgx_base_url: str = "https://dgx.invalid/v1"
    dgx_api_key: SecretStr = SecretStr("")
    dgx_model: str = "configure-model"
    dgx_private_network: bool = False
    model_temperature: float = Field(default=0.3, ge=0, le=2)
    model_max_tokens: int = Field(default=4096, ge=128, le=32768)
    model_timeout_seconds: float = Field(default=90, ge=1, le=300)
    model_retries: int = Field(default=2, ge=0, le=5)
    model_concurrency: int = Field(default=2, ge=1, le=16)
    model_input_cost_per_million: float = Field(default=0, ge=0)
    model_output_cost_per_million: float = Field(default=0, ge=0)
    structured_output_method: str = "json_schema"
    worker_concurrency: int = Field(default=2, ge=1, le=8)
    run_timeout_seconds: int = Field(default=600, ge=30, le=3600)
    approval_ttl_hours: int = Field(default=24, ge=1, le=168)
    rate_limit_per_minute: int = Field(default=10, ge=1, le=100)
    max_request_chars: int = Field(default=8000, ge=100, le=32000)
    max_active_runs_per_user: int = Field(default=5, ge=1, le=20)
    artifact_root: Path = Path("artifacts")
    storage_provider: str = "local"
    s3_bucket: str = ""
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    image_provider: str = "none"
    image_base_url: str = "https://images.invalid/v1"
    image_api_key: SecretStr = SecretStr("")
    image_model: str = "configure-image-model"
    tts_provider: str = "none"
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    media_timeout_seconds: int = Field(default=180, ge=5, le=600)
    subtitle_font: str = "Noto Sans Devanagari"
    n8n_base_url: str = "https://n8n.invalid"
    n8n_token: SecretStr = SecretStr("")
    n8n_workflows: dict[str, str] = Field(default_factory=dict)
    airflow_base_url: str = "https://airflow.invalid/api/v2"
    airflow_token: SecretStr = SecretStr("")
    airflow_allowed_dags: list[str] = Field(default_factory=list)
    research_url: str | None = None
    research_token: SecretStr = SecretStr("")
    research_provider: str = "none"
    otel_exporter_otlp_endpoint: str | None = None
    environment: str = "production"

    @field_validator(
        "dgx_base_url", "image_base_url", "zulip_url", "n8n_base_url", "airflow_base_url"
    )
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("Expected an HTTP(S) URL without embedded credentials")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_providers(self):
        if self.storage_provider not in {"local", "s3"}:
            raise ValueError("Unknown storage provider")
        if self.image_provider not in {"none", "mock", "openai"}:
            raise ValueError("Unknown image provider")
        if self.tts_provider not in {"none", "mock", "elevenlabs"}:
            raise ValueError("Unknown TTS provider")
        if self.research_provider not in {"none", "http"}:
            raise ValueError("Unknown research provider")
        if self.structured_output_method not in {"json_schema", "json_mode", "function_calling"}:
            raise ValueError("Unsupported structured output method")
        if self.dgx_base_url.startswith("http://") and not self.dgx_private_network:
            raise ValueError("Plain HTTP DGX requires an explicitly configured private VPN network")
        return self

    @property
    def checkpoint_url(self) -> str:
        return self.database_url.get_secret_value().replace(
            "postgresql+psycopg://", "postgresql://"
        )

    def require_runtime(self) -> None:
        if len(self.api_token.get_secret_value()) < 32:
            raise ValueError("API_TOKEN must contain at least 32 characters")
        if not self.allowed_user_ids:
            raise ValueError("ALLOWED_USER_IDS must explicitly allow at least one Zulip user")
        if not self.dgx_api_key.get_secret_value() or self.dgx_model == "configure-model":
            raise ValueError("Configure DGX_API_KEY and DGX_MODEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
