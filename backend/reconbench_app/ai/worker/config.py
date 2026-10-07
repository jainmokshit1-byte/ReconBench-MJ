"""AI worker settings and fail-closed startup checks."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from reconbench_app.ai.gate import DeploymentGate
from reconbench_app.extraction.vision import DEFAULT_VISION_READER, vision_reader

# The Gemini SDK reads these as a fallback credential. A process-wide provider
# key would bypass per-workspace keys, consent and session binding.
FORBIDDEN_PROVIDER_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


class AiWorkerStartupError(RuntimeError):
    """The AI worker refuses to start in an unsafe configuration."""


class AiWorkerSettings(BaseSettings):
    """Database, keyring and gate settings; the AI worker needs no object-store access.

    Page images for the vision reader arrive through ``document_page_image``.
    """

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
    ai_keyring_file: Path = Field(validation_alias="RB_AI_KEYRING_FILE")
    ai_external_calls: str = Field(default="disabled", validation_alias="RB_AI_EXTERNAL_CALLS")
    ai_external_calls_approval_ref: str | None = Field(
        default=None, validation_alias="RB_AI_EXTERNAL_CALLS_APPROVAL_REF"
    )
    ai_limits_file: Path | None = Field(default=None, validation_alias="RB_AI_LIMITS_FILE")
    ai_price_file: Path | None = Field(default=None, validation_alias="RB_AI_PRICE_FILE")
    ai_heartbeat_seconds: float = Field(default=10.0, gt=0, le=60)
    vision_reader: str = Field(default=DEFAULT_VISION_READER, validation_alias="RB_VISION_READER")
    log_level: str = "INFO"

    def model_post_init(self, __context: object) -> None:
        vision_reader(self.vision_reader)  # fail at startup on an unknown reader

    def deployment_gate(self) -> DeploymentGate:
        return DeploymentGate.from_values(
            self.ai_external_calls, self.ai_external_calls_approval_ref
        )


def check_startup_environment(environ: Mapping[str, str]) -> None:
    present = sorted(name for name in FORBIDDEN_PROVIDER_ENV if name in environ)
    if present:
        raise AiWorkerStartupError(
            "refusing to start: process-wide provider credentials are set "
            f"({', '.join(present)}); provider keys come only from sealed workspace secrets"
        )


__all__ = [
    "FORBIDDEN_PROVIDER_ENV",
    "AiWorkerSettings",
    "AiWorkerStartupError",
    "check_startup_environment",
]
