"""Application-generated UUIDv7 identifiers for persistent ReconBench rows."""

from __future__ import annotations

import secrets
import time
from uuid import UUID

_MAX_UUID_TIMESTAMP_MS = (1 << 48) - 1


def new_id() -> UUID:
    """Return a UUIDv7; callers must use business keys for ordering and tie-breaks."""

    timestamp_ms = time.time_ns() // 1_000_000
    if not 0 <= timestamp_ms <= _MAX_UUID_TIMESTAMP_MS:
        raise OverflowError("current Unix timestamp cannot be represented by UUIDv7")

    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (timestamp_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return UUID(int=value)


__all__ = ["new_id"]
