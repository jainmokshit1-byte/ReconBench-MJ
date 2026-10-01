"""Infrastructure-only application settings, read once at process startup."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Required connection settings plus optional observability and key settings."""

    model_config = SettingsConfigDict(
        env_prefix="RB_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["local", "ci", "staging", "production"] = Field(
        default="local", validation_alias="RB_ENV"
    )
    database_url: str
    s3_endpoint_url: str
    s3_region: str
    s3_bucket: str
    s3_api_access_key: SecretStr
    s3_api_secret_key: SecretStr
    secret_master_key: SecretStr | None = Field(
        default=None, validation_alias="RB_SECRET_MASTER_KEY"
    )
    secret_master_key_id: str | None = Field(
        default=None, validation_alias="RB_SECRET_MASTER_KEY_ID"
    )
    public_base_url: str
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: str | None = None
    sentry_dsn: str | None = None

    def model_post_init(self, __context: object) -> None:
        if self.secret_master_key is not None and not self.secret_master_key_id:
            raise ValueError("RB_SECRET_MASTER_KEY_ID is required when RB_SECRET_MASTER_KEY is set")
