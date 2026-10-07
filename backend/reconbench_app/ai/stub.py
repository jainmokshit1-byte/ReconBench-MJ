"""Deterministic offline provider for tests and CI; it never makes a network call.

It wraps the existing ``ai_assist.StubProvider`` fixtures (whose offline
``/ai/test`` behaviour is unchanged) and can also replay scripted outputs or
failures so every runner path can be exercised without a provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from reconbench_app.ai.provider import (
    ProviderFailure,
    ProviderRequest,
    ProviderResult,
    ProviderUsage,
)
from reconbench_app.ai_assist import StubProvider


@dataclass
class ScriptedProvider:
    """Returns ``outputs`` in order (strings, or ``ProviderFailure`` to raise)."""

    outputs: list[str | ProviderFailure] = field(default_factory=list)
    name: str = "stub"
    calls: list[ProviderRequest] = field(default_factory=list)
    keys_seen: list[str] = field(default_factory=list)

    def complete(self, request: ProviderRequest, *, api_key: str) -> ProviderResult:
        self.calls.append(request)
        self.keys_seen.append(api_key)
        if not self.outputs:
            raise ProviderFailure("error", delivery="not_sent")
        item = self.outputs.pop(0)
        if isinstance(item, ProviderFailure):
            raise item
        return ProviderResult(
            output_text=item,
            usage=ProviderUsage(
                input_tokens=len(request.input_text), output_tokens=len(item), thought_tokens=0
            ),
        )


def legacy_fixture_output(mode: str) -> str:
    """The original offline stub fixtures serialised as provider output text."""

    return json.dumps(StubProvider().complete(mode))  # type: ignore[arg-type]


__all__ = ["ScriptedProvider", "legacy_fixture_output"]
