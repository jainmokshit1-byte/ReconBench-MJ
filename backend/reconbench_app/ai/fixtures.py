"""Synthetic, repository-owned provider fixtures and their compiled digest allowlist.

Every fixture is invented text with no business data. ``SYNTHETIC_FIXTURE_DIGESTS``
is a literal allowlist: changing a fixture changes its payload digest, and a unit
test fails until the literal is deliberately updated after review.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class SyntheticFixture:
    fixture_id: str
    system_instruction: str
    input_text: str
    response_schema: Mapping[str, Any] = field(default_factory=dict)
    max_output_tokens: int = 32


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

SYNTHETIC_FIXTURES: Mapping[str, SyntheticFixture] = MappingProxyType(
    {PROVIDER_TEST.fixture_id: PROVIDER_TEST}
)

SYNTHETIC_FIXTURE_DIGESTS: Mapping[str, str] = MappingProxyType(
    {
        "provider_test.v1": "470011f5c22fdce9211834df8fd5f083a63107b590a4f18b001fac0b176dc4f1",
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
    "PROVIDER_TEST",
    "SYNTHETIC_FIXTURES",
    "SYNTHETIC_FIXTURE_DIGESTS",
    "SyntheticFixture",
    "plain_schema",
]
