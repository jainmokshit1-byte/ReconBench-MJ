"""H3 handler: an advisory choice among an unlinked invoice's top-five PO candidates.

The candidate pool is rebuilt from the current mapping snapshot and must match
the fingerprint the request was queued with. A valid choice becomes at most one
``suggested`` AI link that a person confirms or rejects through the normal link
workflow; it never supersedes a live link and never feeds balances or verdicts.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from reconbench_app.ai import h3
from reconbench_app.ai.contracts import H3Output
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import H3Candidate, h3_prompt
from reconbench_app.ai.validators import H3Decision, validate_h3
from reconbench_app.ai.worker.handlers import Prepared, Skip
from reconbench_app.mapping_service import MappingService


def _resolution(
    connection: Connection, request: Any, *, for_update: bool = False
) -> h3.ResolutionState | None:
    return h3.load_resolution(
        connection,
        request["workspace_id"],
        UUID(str(request["subject_id"])),
        for_update=for_update,
    )


class PoRerankHandler:
    kind = h3.HOOK

    def prepare(self, connection: Connection, request: Any, limits: AiLimits) -> Prepared | Skip:
        state = _resolution(connection, request)
        if state is None or not state.open:
            return Skip(h3.SKIP_SOURCE_CHANGED)
        snapshot = MappingService(connection.engine).load_snapshot(
            connection, request["workspace_id"]
        )
        # A different hash means a mapping re-run is pending; it re-queues if needed.
        if snapshot is None or snapshot.input_hash != state.input_hash:
            return Skip(h3.SKIP_SOURCE_CHANGED)
        source = next(
            (item for item in snapshot.sources if item.document_id == state.invoice_id), None
        )
        pool = (
            h3.build_pool(source, snapshot.purchase_orders, snapshot.config)
            if source is not None
            else None
        )
        if pool is None or pool.fingerprint != str(request["subject_version"]):
            return Skip(h3.SKIP_SOURCE_CHANGED)
        return Prepared(
            payload=h3_prompt(
                pool.invoice.text,
                [H3Candidate(item.token, item.text.text) for item in pool.candidates],
            ),
            output_kind=h3.HOOK,
            max_output_tokens=limits.h3_rerank.max_output_tokens,
            cacheable=True,
            redacted=pool.redacted,
            context={
                "invoice_text": pool.invoice.text,
                "input_hash": state.input_hash,
                "candidates": {item.token: str(item.po_document_id) for item in pool.candidates},
                "scores": {str(item.po_document_id): str(item.score) for item in pool.candidates},
            },
        )

    def is_current(self, connection: Connection, request: Any) -> bool:
        state = _resolution(connection, request)
        return state is not None and state.open

    def validate(self, prepared: Prepared, parsed: Any) -> H3Decision:
        if not isinstance(parsed, H3Output):
            raise TypeError("unexpected H3 output")
        return validate_h3(
            parsed,
            candidates={
                token: UUID(po_id) for token, po_id in prepared.context["candidates"].items()
            },
            invoice_text=str(prepared.context["invoice_text"]),
        )

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
        del now
        state = _resolution(connection, request, for_update=True)
        if (
            state is None
            or not state.open
            or state.input_hash != str(prepared.context["input_hash"])
        ):
            return "discarded_stale"
        if validated.po_document_id is None or call_log_id is None:
            return "not_applicable"
        recorded = MappingService(connection.engine).record_ai_link_suggestion(
            connection,
            workspace_id=request["workspace_id"],
            invoice_id=state.invoice_id,
            po_document_id=validated.po_document_id,
            score=Decimal(str(prepared.context["scores"][str(validated.po_document_id)])),
            reason=validated.reason,
            ai_call_id=call_log_id,
        )
        return "applied" if recorded else "discarded_stale"


__all__ = ["PoRerankHandler"]
