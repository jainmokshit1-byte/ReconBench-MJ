"""Vision reader port (decision D17, accuracy plan AC-5): which reader reads page images.

The layout engine (``engine.py``) turns a PDF into positioned words; a vision
reader reads *masked page images* into the ``vision.schema.v4`` contract. Two
readers exist, and they are alternatives, never combined (owner decision of 16
Oct 2026): ``vision_gemini`` (Google Gemini) and ``vision_openai_compat`` (a
self-hosted OpenAI-compatible server such as vLLM serving Qwen3.5-9B, LLM plan
WI-BE2). Both run asynchronously in the AI worker through the same gate,
consent, limits and ledger; each request goes to the one provider it names.

``RB_VISION_READER`` selects the reader for the deployment; ``off`` turns page
images off entirely (no page is rendered or sent), whatever a workspace chose.
This module stays import-light: no image or provider libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VISION_READERS = ("vision_gemini", "vision_openai_compat")
VISION_READER_CHOICES = ("off", *VISION_READERS)
DEFAULT_VISION_READER = "vision_gemini"

MediaResolution = Literal["low", "medium", "high", "ultra_high"]


@dataclass(frozen=True, slots=True)
class VisionReaderInfo:
    name: str
    provider: str
    """The ``ai_provider`` the reader calls (the workspace's provider must match)."""
    external: bool
    """Sends data off the server: needs the deployment gate, consent and a key."""
    media_resolution: MediaResolution
    prompt_variant: str
    """Which wording of the shared vision prompt the reader gets."""
    schema_dialect: Literal["gemini", "openai_json_schema"]
    """How the response schema is expressed to the provider."""
    box_order: Literal["yx", "xy"]
    """Coordinate order of the boxes the model returns (``yx`` = ``[ymin, xmin, ymax, xmax]``)."""
    billing: bool
    """Calls are billed per token (cost estimates and reservations apply)."""
    thinking: Literal["low", "off"]
    """The thinking setting every request of this reader carries."""
    sampling_profile: Literal["provider_default", "qwen_card_nonthinking"]
    """Named sampling settings (temperature, top-p …); ``provider_default`` sends none."""
    max_pixels: int | None
    """Image pixel cap the provider applies, when it has one (``None``: not set)."""
    pages_per_call: int | None = None
    """Page images per provider call (``None``: the whole document in one call). Not part
    of ``reader_params``: a per-page answer is merged into one document answer."""
    band_prompt_variant: str | None = None
    """Prompt variant of one extra read of page 1's header band (WI-Q2, D93), whose
    answer adds annotations only (handwritten references, never a field); ``None``: no
    band read. Part of ``reader_params``, so a reader with a band has its own cache key."""


BAND_VERSION = "vision.band.v1"
BAND_TOP = 350
"""Bottom edge of the header band on the 0-1000 page scale: the top 35 % of the upright page."""
BAND_DOC_TYPES = frozenset({"invoice"})
"""Document types whose request carries the band read: handwritten PO/GRN numbers are
written on invoices (D87 truth), and only invoice references feed the matcher's hints."""


@dataclass(frozen=True, slots=True)
class VisionRenderSpec:
    """How a page becomes the image that is sent. Changing it changes every page hash."""

    version: str
    work_dpi: int
    max_side: int
    mime_type: Literal["image/jpeg"]
    jpeg_quality: int


VISION_RENDER = VisionRenderSpec(
    version="vision.render.v1",
    work_dpi=300,
    max_side=2048,
    mime_type="image/jpeg",
    jpeg_quality=90,
)

_READERS = {
    "vision_gemini": VisionReaderInfo(
        name="vision_gemini",
        provider="gemini",
        external=True,
        media_resolution="high",
        prompt_variant="gemini",
        schema_dialect="gemini",
        box_order="yx",
        billing=True,
        thinking="low",
        sampling_profile="provider_default",
        max_pixels=None,
    ),
    # Qwen3.5-9B on vLLM: 8192-token context, so one page per call (plan §0.5). Boxes are
    # asked as [ymin, xmin, ymax, xmax]; the WI-OPS1 image smoke test confirms the order.
    # Prompt vision.prompt.v4.qwen: v5-v7 were each worse on the same-day bench (D75-D77).
    # Full pages with v4 plus the page-1 header band with v4.q2 (WI-Q2, D92/D93): the band
    # typed 11/16 handwritten references, v4 full pages + band 12/16; the full-page v4.q2
    # prompt lost fields and was rejected, so the band answer never sets a field.
    "vision_openai_compat": VisionReaderInfo(
        name="vision_openai_compat",
        provider="vllm",
        external=False,
        media_resolution="high",
        prompt_variant="qwen",
        schema_dialect="openai_json_schema",
        box_order="yx",
        billing=False,
        thinking="off",
        sampling_profile="qwen_card_nonthinking",
        max_pixels=None,
        pages_per_call=1,
        band_prompt_variant="qwen_q2",
    ),
}


class UnknownVisionReader(ValueError):
    pass


def vision_reader(name: str) -> VisionReaderInfo | None:
    """The configured reader, or ``None`` when page images are off."""

    if name == "off":
        return None
    reader = _READERS.get(name)
    if reader is None:
        raise UnknownVisionReader(
            f"RB_VISION_READER must be one of {', '.join(VISION_READER_CHOICES)}; got {name!r}"
        )
    return reader


# The reader each vision provider uses when the workspace picks that provider
# (LLM plan WI-BE3). Providers are separate alternatives; there is no mixed reader.
PROVIDER_READERS = {"gemini": "vision_gemini", "vllm": "vision_openai_compat"}


def reader_for_provider(configured: str, provider: str | None) -> VisionReaderInfo | None:
    """The reader a workspace's chosen vision ``provider`` uses on this deployment.

    ``RB_VISION_READER=off`` turns page images off for every provider. When the
    configured reader belongs to ``provider`` it is used as is (so Gemini keeps
    the exact reader it has today); otherwise the provider's own reader.
    """

    reader = vision_reader(configured)
    if reader is None or provider is None:
        return None
    if reader.provider == provider:
        return reader
    name = PROVIDER_READERS.get(provider)
    return _READERS[name] if name is not None else None


__all__ = [
    "BAND_DOC_TYPES",
    "BAND_TOP",
    "BAND_VERSION",
    "DEFAULT_VISION_READER",
    "PROVIDER_READERS",
    "VISION_READERS",
    "VISION_READER_CHOICES",
    "VISION_RENDER",
    "MediaResolution",
    "UnknownVisionReader",
    "VisionReaderInfo",
    "VisionRenderSpec",
    "reader_for_provider",
    "vision_reader",
]
