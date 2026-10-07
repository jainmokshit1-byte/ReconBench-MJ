"""Operator limits and optional model prices for AI calls (AI Assist plan §5.3).

Built-in defaults apply when no limits file is configured. A configured file that
is missing or invalid makes the status "Limits not configured" and blocks calls.
Token reservations are estimates (no local tokenizer was proven in Phase 1), so a
guaranteed token cap or USD hard cap is never claimed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

INPUT_TOKEN_OVERHEAD = 512
# No local tokenizer is available and template overhead is unknown (Phase 1).
TOKEN_BOUND_GUARANTEED = False


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CallKindLimits(_Frozen):
    max_input_bytes: int = Field(ge=100, le=1_000_000)
    max_output_tokens: int = Field(ge=16, le=65_536)
    max_pages: int | None = Field(default=None, ge=1, le=50)
    max_pairs: int | None = Field(default=None, ge=1, le=20)
    # Page images (h6_vision): total image bytes per call, and the input tokens each
    # image is *estimated* to cost (no local tokenizer; the owner probe measures it).
    max_image_bytes: int | None = Field(default=None, ge=10_000, le=20_000_000)
    image_tokens_estimate: int | None = Field(default=None, ge=1, le=20_000)
    # Overrides the global call timeout for this kind (the transport guard caps 120 s).
    call_timeout_seconds: int | None = Field(default=None, ge=1, le=120)


def _kind(**values: int) -> CallKindLimits:
    return CallKindLimits(**values)


class AiLimits(_Frozen):
    call_timeout_seconds: int = Field(default=60, ge=1, le=120)
    queue_timeout_seconds: int = Field(default=1_800, ge=60, le=86_400)
    h1_extract: CallKindLimits = _kind(max_input_bytes=40_000, max_output_tokens=4_096, max_pages=8)
    h2_item: CallKindLimits = _kind(max_input_bytes=12_000, max_output_tokens=1_024, max_pairs=20)
    h3_rerank: CallKindLimits = _kind(max_input_bytes=24_000, max_output_tokens=256)
    h4_vendor: CallKindLimits = _kind(max_input_bytes=2_000, max_output_tokens=128)
    h6_vision: CallKindLimits = _kind(
        max_input_bytes=16_000,
        max_output_tokens=16_384,
        max_pages=8,
        max_image_bytes=12_000_000,
        # Owner probe (7 Oct 2026): about 1,100 input tokens per page at "high"; 1,200 keeps margin.
        image_tokens_estimate=1_200,
        call_timeout_seconds=120,
    )
    synthetic: CallKindLimits = _kind(max_input_bytes=1_000, max_output_tokens=32)
    thought_reserve_tokens: int = Field(default=0, ge=0, le=65_536)
    per_operation_calls: int = Field(default=25, ge=1, le=10_000)
    per_operation_tokens: int = Field(default=150_000, ge=1_000, le=100_000_000)
    per_day_calls: int = Field(default=200, ge=1, le=100_000)
    per_day_tokens: int = Field(default=1_000_000, ge=1_000, le=1_000_000_000)
    # Calendar-month caps (UTC); off until the owner sets them (decision D-8).
    per_month_calls: int | None = Field(default=None, ge=1, le=10_000_000)
    per_month_tokens: int | None = Field(default=None, ge=1_000, le=100_000_000_000)
    per_minute_calls: int = Field(default=10, ge=1, le=1_000)
    in_flight: int = Field(default=2, ge=1, le=50)
    cooldown_seconds: int = Field(default=60, ge=1, le=3_600)
    cache_ttl_days: int = Field(default=30, ge=1, le=365)

    def for_kind(self, kind: str) -> CallKindLimits:
        if kind in {"provider_test", "synthetic_smoke"}:
            return self.synthetic
        limits = getattr(self, kind, None)
        if not isinstance(limits, CallKindLimits):
            raise KeyError(kind)
        return limits

    def timeout_for(self, kind: str) -> int:
        override = self.for_kind(kind).call_timeout_seconds
        return override if override is not None else self.call_timeout_seconds


class ModelPrice(_Frozen):
    input_usd_per_million: Decimal = Field(ge=0, le=Decimal("1000"))
    output_usd_per_million: Decimal = Field(ge=0, le=Decimal("1000"))


class PriceFile(_Frozen):
    price_version: str = Field(min_length=1, max_length=64)
    models: dict[str, ModelPrice]


@dataclass(frozen=True, slots=True)
class LimitsState:
    limits: AiLimits | None

    @property
    def configured(self) -> bool:
        return self.limits is not None


def load_limits(path: Path | None) -> LimitsState:
    if path is None:
        return LimitsState(AiLimits())
    try:
        return LimitsState(AiLimits.model_validate(json.loads(path.read_text(encoding="utf-8"))))
    except (OSError, ValueError, ValidationError):
        return LimitsState(None)


def load_prices(path: Path | None) -> PriceFile | None:
    if path is None:
        return None
    try:
        return PriceFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError):
        return None


@dataclass(frozen=True, slots=True)
class Reservation:
    input_tokens: int
    output_tokens: int
    is_estimate: bool
    usd: Decimal | None
    price_version: str | None


def estimate_reservation(
    *,
    payload_bytes: int,
    max_output_tokens: int,
    limits: AiLimits,
    model: str,
    prices: PriceFile | None,
    image_tokens: int = 0,
) -> Reservation:
    """Worst-case-ish reservation: one token per byte plus overhead (an estimate).

    ``image_tokens`` is the estimated cost of the page images sent with the text.
    """

    input_tokens = payload_bytes + INPUT_TOKEN_OVERHEAD + image_tokens
    output_tokens = max_output_tokens + limits.thought_reserve_tokens
    price = prices.models.get(model) if prices else None
    usd = None
    if price is not None:
        usd = cost_usd(price, input_tokens=input_tokens, output_tokens=output_tokens)
    return Reservation(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        is_estimate=not TOKEN_BOUND_GUARANTEED,
        usd=usd,
        price_version=prices.price_version if price is not None and prices else None,
    )


def cost_usd(price: ModelPrice, *, input_tokens: int, output_tokens: int) -> Decimal:
    million = Decimal(1_000_000)
    value = (
        Decimal(input_tokens) * price.input_usd_per_million
        + Decimal(output_tokens) * price.output_usd_per_million
    ) / million
    return value.quantize(Decimal("0.000001"))


def usd_cap_offered(model: str, prices: PriceFile | None) -> bool:
    """A USD cap needs a priced model *and* a guaranteed token bound."""

    return TOKEN_BOUND_GUARANTEED and prices is not None and model in prices.models


__all__ = [
    "INPUT_TOKEN_OVERHEAD",
    "TOKEN_BOUND_GUARANTEED",
    "AiLimits",
    "CallKindLimits",
    "LimitsState",
    "ModelPrice",
    "PriceFile",
    "Reservation",
    "cost_usd",
    "estimate_reservation",
    "load_limits",
    "load_prices",
    "usd_cap_offered",
]
