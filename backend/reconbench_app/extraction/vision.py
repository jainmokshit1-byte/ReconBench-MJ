"""Vision reader port (decision D17, accuracy plan AC-5): which reader reads page images.

The layout engine (``engine.py``) turns a PDF into positioned words; a vision
reader reads *masked page images* into the ``vision.schema.v2`` contract. Only
``vision_gemini`` exists: it runs asynchronously in the AI worker through the
existing gate, consent, key, limits and ledger. A local model can be added later
as another entry here (``external=False``) behind the same handler.

``RB_VISION_READER`` selects the reader for the deployment; ``off`` turns page
images off entirely (no page is rendered or sent), whatever a workspace chose.
This module stays import-light: no image or provider libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VISION_READERS = ("vision_gemini",)
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
        name="vision_gemini", provider="gemini", external=True, media_resolution="high"
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


__all__ = [
    "DEFAULT_VISION_READER",
    "VISION_READERS",
    "VISION_READER_CHOICES",
    "VISION_RENDER",
    "MediaResolution",
    "UnknownVisionReader",
    "VisionReaderInfo",
    "VisionRenderSpec",
    "vision_reader",
]
