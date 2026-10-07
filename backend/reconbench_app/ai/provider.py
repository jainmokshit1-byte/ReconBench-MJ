"""Provider-neutral port for one structured AI call (decision D1)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# "responded": the provider answered (the call finished, possibly billed).
# "not_sent": provably never reached the provider. "may_have_been_sent": unknown.
Delivery = Literal["responded", "not_sent", "may_have_been_sent"]


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
    thinking_level: Literal["low"] = "low"
    # Page images (h6_vision); text-only hooks send none and keep their exact shape.
    images: tuple[ProviderImage, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderResult:
    output_text: str
    usage: ProviderUsage | None


class ProviderFailure(RuntimeError):
    """A sanitized failure: never carries provider messages or key material."""

    def __init__(
        self,
        code: str,
        *,
        delivery: Delivery,
        cooldown_seconds: int | None = None,
        usage: ProviderUsage | None = None,
    ) -> None:
        self.code = code
        self.delivery = delivery
        self.cooldown_seconds = cooldown_seconds
        self.usage = usage
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
]
