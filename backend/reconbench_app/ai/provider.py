"""Provider-neutral port for one structured AI call (decision D1)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# "responded": the provider answered (the call finished, possibly billed).
# "not_sent": provably never reached the provider. "may_have_been_sent": unknown.
Delivery = Literal["responded", "not_sent", "may_have_been_sent"]
ThinkingLevel = Literal["minimal", "low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int
    output_tokens: int
    thought_tokens: int | None


@dataclass(frozen=True, slots=True)
class ProviderImage:
    """One page image sent after ``caption`` (for example "Page 2")."""

    caption: str
    mime_type: Literal["image/jpeg", "image/png"]
    data: bytes = field(repr=False)
    sha256: str
    resolution: Literal["low", "medium", "high", "ultra_high"] = "high"


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    model: str
    system_instruction: str
    input_text: str
    response_schema: Mapping[str, Any]
    max_output_tokens: int
    timeout_ms: int
    # ``None`` leaves thinking to the provider's own default.
    thinking_level: ThinkingLevel | None = "low"
    # Page images (h6_vision); text-only hooks send none and keep their exact shape.
    images: tuple[ProviderImage, ...] = ()
    # Provider-specific settings (sampling, chat-template kwargs); a provider ignores
    # keys it does not know. Gemini reads none today.
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    output_text: str
    usage: ProviderUsage | None
    latency_ms: int | None = None
    finish_reason: str | None = None
    model_reported: str | None = None
    """The model id the provider says answered (may differ from the requested alias)."""
    reasoning_present: bool = False
    """The answer carried a separate reasoning/thinking part."""


class ProviderFailure(RuntimeError):
    """A sanitized failure: never carries provider messages or key material."""

    def __init__(
        self,
        code: str,
        *,
        delivery: Delivery,
        cooldown_seconds: int | None = None,
        usage: ProviderUsage | None = None,
        detail: str | None = None,
    ) -> None:
        self.code = code
        self.delivery = delivery
        self.cooldown_seconds = cooldown_seconds
        self.usage = usage
        # A short sanitized diagnostic (no key, digit runs masked); never shown to users.
        self.detail = detail
        super().__init__(f"provider call failed: {code} ({delivery})")


class AiProvider(Protocol):
    name: str

    def complete(self, request: ProviderRequest, *, api_key: str) -> ProviderResult: ...


__all__ = [
    "AiProvider",
    "Delivery",
    "ProviderFailure",
    "ProviderImage",
    "ProviderRequest",
    "ProviderResult",
    "ProviderUsage",
    "ThinkingLevel",
]
