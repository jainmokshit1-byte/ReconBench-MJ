"""Synthetic, repository-owned provider fixtures and their compiled digest allowlist.

Every fixture is invented text with no business data. ``SYNTHETIC_FIXTURE_DIGESTS``
is a literal allowlist: changing a fixture changes its payload digest, and a unit
test fails until the literal is deliberately updated after review.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from reconbench_app.ai.provider import ProviderImage


@dataclass(frozen=True, slots=True)
class SyntheticFixture:
    fixture_id: str
    system_instruction: str
    input_text: str
    response_schema: Mapping[str, Any] = field(default_factory=dict)
    max_output_tokens: int = 32
    images: tuple[ProviderImage, ...] = ()


PROVIDER_TEST = SyntheticFixture(
    fixture_id="provider_test.v1",
    system_instruction=(
        "You are a connectivity check. Return only JSON matching the supplied schema."
    ),
    input_text="Synthetic ReconBench provider test. Return status ok.",
    response_schema=MappingProxyType(
        {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["ok"]}},
            "required": ["status"],
            "additionalProperties": False,
        }
    ),
    max_output_tokens=32,
)

# 5x7 glyphs of the digits drawn in the image test (``#`` is ink).
_GLYPHS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
        "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
        "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    }
)
_SCALE = 12
_MARGIN = 40
_GAP = 24


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _digits_png(text: str) -> bytes:
    """Black digits on white as an 8-bit grayscale PNG, byte-identical everywhere.

    The image data is one *stored* (uncompressed) deflate block, so the bytes do not
    depend on the zlib version and the payload digest stays on the allowlist on every host.
    """

    width = 2 * _MARGIN + len(text) * 5 * _SCALE + (len(text) - 1) * _GAP
    height = 2 * _MARGIN + 7 * _SCALE
    rows = []
    for y in range(height):
        row = bytearray(b"\xff" * width)
        if 0 <= y - _MARGIN < 7 * _SCALE:
            glyph_row = (y - _MARGIN) // _SCALE
            for index, digit in enumerate(text):
                left = _MARGIN + index * (5 * _SCALE + _GAP)
                for column, cell in enumerate(_GLYPHS[digit][glyph_row]):
                    if cell == "#":
                        start = left + column * _SCALE
                        row[start : start + _SCALE] = b"\x00" * _SCALE
        rows.append(b"\x00" + bytes(row))  # filter type 0 (None)
    raw = b"".join(rows)
    if len(raw) > 0xFFFF:
        raise ValueError("synthetic image too large for one stored block")
    stored = (
        b"\x78\x01"  # zlib header
        + b"\x01"  # the final block, stored
        + struct.pack("<HH", len(raw), len(raw) ^ 0xFFFF)
        + raw
        + struct.pack(">I", zlib.adler32(raw))
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", stored)
        + _png_chunk(b"IEND", b"")
    )


PROVIDER_IMAGE_TEST_NUMBER = "472"
_PROVIDER_IMAGE_TEST_PNG = _digits_png(PROVIDER_IMAGE_TEST_NUMBER)

# Server-side vision check (PD-1b): a model that cannot see the image cannot name the number.
PROVIDER_IMAGE_TEST = SyntheticFixture(
    fixture_id="provider_image_test.v1",
    system_instruction=(
        "You are an image capability check. Return only JSON matching the supplied schema."
    ),
    input_text=(
        "Synthetic ReconBench image test. The image shows a three-digit number in black "
        "on white. Return that number."
    ),
    response_schema=MappingProxyType(
        {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
            "additionalProperties": False,
        }
    ),
    max_output_tokens=32,
    images=(
        ProviderImage(
            caption="Synthetic test image",
            mime_type="image/png",
            data=_PROVIDER_IMAGE_TEST_PNG,
            sha256=hashlib.sha256(_PROVIDER_IMAGE_TEST_PNG).hexdigest(),
        ),
    ),
)

SYNTHETIC_FIXTURES: Mapping[str, SyntheticFixture] = MappingProxyType(
    {
        PROVIDER_TEST.fixture_id: PROVIDER_TEST,
        PROVIDER_IMAGE_TEST.fixture_id: PROVIDER_IMAGE_TEST,
    }
)

SYNTHETIC_FIXTURE_DIGESTS: Mapping[str, str] = MappingProxyType(
    {
        "provider_test.v1": "470011f5c22fdce9211834df8fd5f083a63107b590a4f18b001fac0b176dc4f1",
        "provider_image_test.v1": (
            "43aec24ee84b3a066fdaa7201a91293c00e13866a38b86f31bf634985e301d34"
        ),
    }
)


def plain_schema(fixture: SyntheticFixture) -> dict[str, Any]:
    """Return the fixture schema as plain JSON-compatible containers."""

    def plain(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        return value

    return plain(fixture.response_schema)  # type: ignore[no-any-return]


__all__ = [
    "PROVIDER_IMAGE_TEST",
    "PROVIDER_IMAGE_TEST_NUMBER",
    "PROVIDER_TEST",
    "SYNTHETIC_FIXTURES",
    "SYNTHETIC_FIXTURE_DIGESTS",
    "SyntheticFixture",
    "plain_schema",
]
