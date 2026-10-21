"""Infrastructure-only application settings, read once at process startup."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.extraction.engine import DEFAULT_ENGINE, engine_for
from reconbench_app.extraction.vision import DEFAULT_VISION_READER, vision_reader


class Settings(BaseSettings):
    """Required connection settings plus optional observability and key settings."""

    model_config = SettingsConfigDict(
        env_prefix="RB_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
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
    ai_seal_public_key_file: Path | None = Field(
        default=None, validation_alias="RB_AI_SEAL_PUBLIC_KEY_FILE"
    )
    ai_external_calls: str = Field(default="disabled", validation_alias="RB_AI_EXTERNAL_CALLS")
    ai_external_calls_approval_ref: str | None = Field(
        default=None, validation_alias="RB_AI_EXTERNAL_CALLS_APPROVAL_REF"
    )
    # Company vLLM server (WI-BE2/BE3): the API needs the gate mode and the endpoint
    # allowlist to check a workspace's server URL on save (SSRF guard); it never calls it.
    ai_vllm_calls: str = Field(default="disabled", validation_alias="RB_AI_VLLM_CALLS")
    ai_vllm_endpoints: str | None = Field(default=None, validation_alias="RB_AI_VLLM_ENDPOINTS")
    ai_limits_file: Path | None = Field(default=None, validation_alias="RB_AI_LIMITS_FILE")
    ai_price_file: Path | None = Field(default=None, validation_alias="RB_AI_PRICE_FILE")
    public_base_url: str
    pdf_ocr_languages: str = Field(
        default="eng",
        pattern=r"^[a-z0-9_]+(?:\+[a-z0-9_]+)*$",
        validation_alias="RB_PDF_OCR_LANGUAGES",
    )
    # PEM (or DER) file with the e-invoice portal's public key(s); when set, a QR
    # whose signature verifies is trusted as a signed source (accuracy plan AC-2).
    einvoice_public_key_file: Path | None = Field(
        default=None, validation_alias="RB_EINVOICE_PUBLIC_KEY_FILE"
    )
    # Layout engine for PDFs (D17 port); the accuracy bench reads the same variable.
    extraction_engine: str = Field(default=DEFAULT_ENGINE, validation_alias="RB_EXTRACTION_ENGINE")
    # Vision reader for page images (D17 port, AC-5): "vision_gemini",
    # "vision_openai_compat" (self-hosted vLLM, WI-BE2) or "off".
    vision_reader: str = Field(default=DEFAULT_VISION_READER, validation_alias="RB_VISION_READER")
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost_kib: int = Field(default=65_536, ge=8_192)
    argon2_parallelism: int = Field(default=4, ge=1)
    argon2_hash_len: int = Field(default=32, ge=16)
    argon2_salt_len: int = Field(default=16, ge=8)
    reconciliation_processes: int = Field(
        default=1,
        ge=1,
        le=8,
        validation_alias="RB_RECONCILIATION_PROCESSES",
    )
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: str | None = None
    sentry_dsn: str | None = None

    def model_post_init(self, __context: object) -> None:
        self.deployment_gate()
        vision_reader(self.vision_reader)
        engine_for(self.extraction_engine)

    def deployment_gate(self) -> DeploymentGate:
        return DeploymentGate.from_values(
            self.ai_external_calls,
            self.ai_external_calls_approval_ref,
            vllm_mode=self.ai_vllm_calls,
            vllm_endpoints=self.ai_vllm_endpoints,
        )
