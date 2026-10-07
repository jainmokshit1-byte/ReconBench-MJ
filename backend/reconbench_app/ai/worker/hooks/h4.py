"""H4 handler: an advisory same/different opinion on an undecided vendor-merge pair.

The opinion is attached to the pending suggestion only. It never merges vendors,
never decides the suggestion and never changes its version, so a person's
decision and its ``If-Match`` are unaffected.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from reconbench_app.ai import h4
from reconbench_app.ai.contracts import H4Output
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import h4_prompt
from reconbench_app.ai.validators import H4Decision, validate_h4
from reconbench_app.ai.worker.handlers import Prepared, Skip


def _pair(
    connection: Connection, request: Any, *, for_update: bool = False
) -> h4.VendorPair | None:
    return h4.load_vendor_pair(
        connection,
        request["workspace_id"],
        UUID(str(request["subject_id"])),
        for_update=for_update,
    )


class VendorOpinionHandler:
    kind = h4.HOOK

    def prepare(self, connection: Connection, request: Any, limits: AiLimits) -> Prepared | Skip:
        pair = _pair(connection, request)
        if pair is None or not pair.open_at(str(request["subject_version"])):
            return Skip(h4.SKIP_SOURCE_CHANGED)
        if pair.pan_conflict:
            return Skip(h4.SKIP_PAN_CONFLICT)
        return Prepared(
            payload=h4_prompt(pair.vendor_a.text, pair.vendor_b.text),
            output_kind=h4.HOOK,
            max_output_tokens=limits.h4_vendor.max_output_tokens,
            cacheable=True,
            redacted=bool(pair.vendor_a.spans or pair.vendor_b.spans),
            context={"vendor_a_text": pair.vendor_a.text, "vendor_b_text": pair.vendor_b.text},
        )

    def is_current(self, connection: Connection, request: Any) -> bool:
        pair = _pair(connection, request)
        return (
            pair is not None
            and pair.open_at(str(request["subject_version"]))
            and not pair.pan_conflict
        )

    def validate(self, prepared: Prepared, parsed: Any) -> H4Decision:
        if not isinstance(parsed, H4Output):
            raise TypeError("unexpected H4 output")
        return validate_h4(
            parsed,
            vendor_a_text=str(prepared.context["vendor_a_text"]),
            vendor_b_text=str(prepared.context["vendor_b_text"]),
            pan_conflict=False,
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
        del prepared, now
        pair = _pair(connection, request, for_update=True)
        if pair is None or not pair.open_at(str(request["subject_version"])):
            return "discarded_stale"
        if pair.pan_conflict or call_log_id is None:
            return "not_applicable"
        recorded = h4.record_opinion(
            connection,
            request["workspace_id"],
            pair,
            opinion=validated.opinion,
            reason=validated.reason,
            call_log_id=call_log_id,
        )
        return "applied" if recorded else "discarded_stale"


__all__ = ["VendorOpinionHandler"]
