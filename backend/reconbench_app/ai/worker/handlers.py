"""Per-call-kind handlers: build the payload, validate output and apply it.

A handler decides what a request sends and how a validated result becomes an
advisory row or draft suggestion. Hook handlers (H1–H4) register here too; the
runner treats every kind identically for gating, budgets and the ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.ai import connection_state
from reconbench_app.ai.contracts import InvalidOutput, ProviderImageTestOutput, ProviderTestOutput
from reconbench_app.ai.fixtures import (
    PROVIDER_IMAGE_TEST,
    PROVIDER_IMAGE_TEST_NUMBER,
    PROVIDER_TEST,
)
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import (
    PromptPayload,
    provider_image_test_prompt,
    provider_test_prompt,
)
from reconbench_app.db.metadata import metadata


@dataclass(frozen=True, slots=True)
class Prepared:
    payload: PromptPayload
    output_kind: str
    max_output_tokens: int
    fixture_id: str | None = None
    cacheable: bool = False
    redacted: bool = False
    document_id: UUID | None = None
    document_version_id: UUID | None = None
    document_hash: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    # A reader that reads one page per call (WI-BE2): one payload per provider call,
    # and how their answers become one answer. ``payload`` stays the whole request
    # (digest, cache key, input limits); empty ``parts`` sends ``payload`` once.
    parts: tuple[PromptPayload, ...] = ()
    merge_outputs: Callable[[Sequence[str]], str] | None = None


@dataclass(frozen=True, slots=True)
class Skip:
    code: str


class Handler(Protocol):
    kind: str

    def prepare(
        self, connection: Connection, request: Any, limits: AiLimits
    ) -> Prepared | Skip: ...

    def is_current(self, connection: Connection, request: Any) -> bool: ...

    def validate(self, prepared: Prepared, parsed: Any) -> Any: ...

    def normalized(self, parsed: Any) -> dict[str, Any]: ...

    def apply(
        self,
        connection: Connection,
        request: Any,
        prepared: Prepared,
        validated: Any,
        *,
        call_log_id: UUID | None,
        now: datetime,
    ) -> str: ...


class ProviderTestHandler:
    """Sends the fixed synthetic fixture and records a passing test for this settings version.

    The image test (PD-1b) sends the synthetic number image instead; its answer must name
    that number, so a model that cannot see the image does not pass.
    """

    kind = "provider_test"

    def prepare(self, connection: Connection, request: Any, limits: AiLimits) -> Prepared | Skip:
        del connection
        if connection_state.request_kind(request) == "image":
            return Prepared(
                payload=provider_image_test_prompt(),
                output_kind="provider_image_test",
                max_output_tokens=min(
                    PROVIDER_IMAGE_TEST.max_output_tokens,
                    limits.synthetic_image.max_output_tokens,
                ),
                fixture_id=PROVIDER_IMAGE_TEST.fixture_id,
            )
        return Prepared(
            payload=provider_test_prompt(),
            output_kind="provider_test",
            max_output_tokens=min(
                PROVIDER_TEST.max_output_tokens, limits.synthetic.max_output_tokens
            ),
            fixture_id=PROVIDER_TEST.fixture_id,
        )

    def is_current(self, connection: Connection, request: Any) -> bool:
        del connection, request
        return True

    def validate(
        self, prepared: Prepared, parsed: Any
    ) -> ProviderTestOutput | ProviderImageTestOutput:
        if prepared.output_kind == "provider_image_test":
            if not isinstance(parsed, ProviderImageTestOutput):
                raise TypeError("unexpected provider image test output")
            if parsed.number.strip() != PROVIDER_IMAGE_TEST_NUMBER:
                raise InvalidOutput("invalid_output", "image test number not read")
            return parsed
        if not isinstance(parsed, ProviderTestOutput):
            raise TypeError("unexpected provider test output")
        return parsed

    def normalized(self, parsed: Any) -> dict[str, Any]:
        return dict(parsed.model_dump())

    def apply(
        self,
        connection: Connection,
        request: Any,
        prepared: Prepared,
        validated: Any,
        *,
        call_log_id: UUID | None,
        now: datetime,
    ) -> str:
        del validated, call_log_id
        window = prepared.context.get("context_window")
        updated = _record_test(
            connection,
            request,
            now,
            ok=True,
            context_window=window if isinstance(window, int) else None,
        )
        return "applied" if updated else "discarded_stale"


def _record_test(
    connection: Connection,
    request: Any,
    now: datetime,
    *,
    ok: bool,
    error_code: str | None = None,
    context_window: int | None = None,
) -> int:
    """Store a test result for the settings version it ran under.

    A plain test keeps its legacy result for Gemini on ``ai_setting``. Every provider's
    card takes the result through ``connection_state`` (Connected / Error, health, and the
    test-then-swap of a key rotation), so a stale result changes nothing.
    """

    legacy = 0
    if request["provider"] != "vllm" and connection_state.request_kind(request) == "test":
        settings = metadata.tables["ai_setting"]
        legacy = int(
            connection.execute(
                sa.update(settings)
                .where(
                    settings.c.workspace_id == request["workspace_id"],
                    settings.c.version == request["settings_version"],
                )
                .values(last_test_at=now, last_test_ok=ok)
            ).rowcount
        )
    card = connection_state.record_result(
        connection, request, now, ok=ok, error_code=error_code, context_window=context_window
    )
    return legacy or card


def record_test_failure(
    connection: Connection, request: Any, now: datetime, error_code: str | None = None
) -> None:
    if request["call_kind"] != "provider_test":
        return
    _record_test(connection, request, now, ok=False, error_code=error_code)


def default_handlers(
    *,
    vision_reader: str | None = None,
    consensus_enqueue: Any | None = None,
) -> dict[str, Handler]:
    # Hook modules import ``Prepared`` and ``Skip`` from here.
    from reconbench_app.ai.worker.hooks.h1 import ExtractionSuggestionHandler
    from reconbench_app.ai.worker.hooks.h2 import ItemOpinionHandler
    from reconbench_app.ai.worker.hooks.h3 import PoRerankHandler
    from reconbench_app.ai.worker.hooks.h4 import VendorOpinionHandler
    from reconbench_app.ai.worker.hooks.vision import VisionReadHandler

    handlers: list[Handler] = [
        ProviderTestHandler(),
        ExtractionSuggestionHandler(),
        ItemOpinionHandler(),
        PoRerankHandler(),
        VendorOpinionHandler(),
        VisionReadHandler(vision_reader, consensus_enqueue=consensus_enqueue)
        if vision_reader
        else VisionReadHandler(consensus_enqueue=consensus_enqueue),
    ]
    return {handler.kind: handler for handler in handlers}


__all__ = [
    "Handler",
    "Prepared",
    "ProviderTestHandler",
    "Skip",
    "default_handlers",
    "record_test_failure",
]
