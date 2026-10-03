"""GSTIN format and ISO-style alphanumeric checksum validation."""

from __future__ import annotations

import re
from dataclasses import dataclass

_GSTIN_PATTERN = re.compile(
    r"^(?P<state>\d{2})(?P<pan>[A-Z]{5}\d{4}[A-Z])"
    r"(?P<entity>[A-Z0-9])Z(?P<check>[A-Z0-9])$"
)
_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class GSTINValidation:
    normalized: str
    format_valid: bool
    checksum_valid: bool
    state_code: str | None
    pan: str | None

    @property
    def is_valid(self) -> bool:
        return self.format_valid and self.checksum_valid


def calculate_gstin_checksum(body: str) -> str:
    """Calculate the final GSTIN character for a 14-character GSTIN body."""

    normalized = re.sub(r"\s+", "", body).upper()
    if len(normalized) != 14 or any(char not in _BASE36 for char in normalized):
        raise ValueError("GSTIN checksum input must be 14 alphanumeric characters")

    factor = 2
    total = 0
    for character in reversed(normalized):
        product = factor * _BASE36.index(character)
        total += product // 36 + product % 36
        factor = 1 if factor == 2 else 2
    return _BASE36[(36 - total % 36) % 36]


def validate_gstin(value: str) -> GSTINValidation:
    """Normalize a GSTIN and report its shape, check character, PAN, and state."""

    normalized = re.sub(r"\s+", "", value).upper()
    match = _GSTIN_PATTERN.fullmatch(normalized)
    if match is None:
        return GSTINValidation(
            normalized=normalized,
            format_valid=False,
            checksum_valid=False,
            state_code=None,
            pan=None,
        )

    expected = calculate_gstin_checksum(normalized[:14])
    return GSTINValidation(
        normalized=normalized,
        format_valid=True,
        checksum_valid=normalized[-1] == expected,
        state_code=match.group("state"),
        pan=match.group("pan"),
    )
