"""Per-call-kind handlers: build the payload, validate output and apply it.

A handler decides what a request sends and how a validated result becomes an
advisory row or draft suggestion. Hook handlers (H1–H4) register here too; the
runner treats every kind identically for gating, budgets and the ledger.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.ai.contracts import ProviderTestOutput
from reconbench_app.ai.fixtures import PROVIDER_TEST
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import PromptPayload, provider_test_prompt
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
    """Sends the fixed synthetic fixture and records a passing test for this settings version."""

    kind = "provider_test"

    def prepare(self, connection: Connection, request: Any, limits: AiLimits) -> Prepared | Skip:
        del connection, request
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

    def validate(self, prepared: Prepared, parsed: Any) -> ProviderTestOutput:
        del prepared
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
        del prepared, validated, call_log_id
        settings = metadata.tables["ai_setting"]
        updated = connection.execute(
            sa.update(settings)
            .where(
                settings.c.workspace_id == request["workspace_id"],
                settings.c.version == request["settings_version"],
            )
            .values(last_test_at=now, last_test_ok=True)
        ).rowcount
        return "applied" if updated else "discarded_stale"


def record_test_failure(connection: Connection, request: Any, now: datetime) -> None:
    if request["call_kind"] != "provider_test":
        return
    settings = metadata.tables["ai_setting"]
    connection.execute(
        sa.update(settings)
        .where(
            settings.c.workspace_id == request["workspace_id"],
            settings.c.version == request["settings_version"],
        )
        .values(last_test_at=now, last_test_ok=False)
    )


def default_handlers(*, vision_reader: str | None = None) -> dict[str, Handler]:
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
        VisionReadHandler(vision_reader) if vision_reader else VisionReadHandler(),
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
