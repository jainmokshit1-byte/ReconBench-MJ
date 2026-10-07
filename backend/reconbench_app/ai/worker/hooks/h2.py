"""H2 handler: an advisory same/different/unsure opinion on uncertain run lines.

The batch is rebuilt from the run's immutable line results and must match the
fingerprint the request was queued with. Each grounded opinion is appended to
``ai_line_opinion`` beside its line result; the run, its cases and its line
results are never changed, and an opinion cannot override a spec conflict
because such lines are never sent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from reconbench_app.ai import h2
from reconbench_app.ai.contracts import H2Output
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import H2PairText, h2_prompt
from reconbench_app.ai.validators import GroundedOpinion, validate_h2
from reconbench_app.ai.worker.handlers import Prepared, Skip
from reconbench_app.db.ids import new_id


def _run_id(request: Any) -> UUID:
    return UUID(str(request["subject_id"]))


class ItemOpinionHandler:
    kind = h2.HOOK

    def prepare(self, connection: Connection, request: Any, limits: AiLimits) -> Prepared | Skip:
        batch = h2.load_batch(
            connection, request["workspace_id"], _run_id(request), str(request["subject_version"])
        )
        max_pairs = limits.h2_item.max_pairs or h2.MAX_PAIRS
        if batch is None or len(batch.pairs) > max_pairs:
            return Skip(h2.SKIP_SOURCE_CHANGED)
        return Prepared(
            payload=h2_prompt(batch.pairs),
            output_kind=h2.HOOK,
            max_output_tokens=limits.h2_item.max_output_tokens,
            cacheable=True,
            redacted=batch.redacted,
            context={
                "pairs": {pair.pair_id: (pair.invoice_text, pair.po_text) for pair in batch.pairs},
                "lines": {
                    pair_id: (
                        str(line.line_result_id),
                        str(line.invoice_line_id),
                        str(line.po_doc_line_id),
                    )
                    for pair_id, line in batch.line_by_pair.items()
                },
            },
        )

    def is_current(self, connection: Connection, request: Any) -> bool:
        return h2.run_succeeded(connection, request["workspace_id"], _run_id(request))

    def validate(self, prepared: Prepared, parsed: Any) -> tuple[GroundedOpinion, ...]:
        if not isinstance(parsed, H2Output):
            raise TypeError("unexpected H2 output")
        opinions, _dropped = validate_h2(
            parsed,
            pairs={
                pair_id: H2PairText(pair_id, invoice_text, po_text)
                for pair_id, (invoice_text, po_text) in prepared.context["pairs"].items()
            },
        )
        return opinions

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
        if call_log_id is None or not validated:
            return "not_applicable"
        lines = prepared.context["lines"]
        values = [
            {
                "id": new_id(),
                "workspace_id": request["workspace_id"],
                "run_id": _run_id(request),
                "recon_line_result_id": UUID(lines[item.pair_id][0]),
                "invoice_line_id": UUID(lines[item.pair_id][1]),
                "po_doc_line_id": UUID(lines[item.pair_id][2]),
                "ai_request_id": request["id"],
                "ai_call_id": call_log_id,
                "opinion": item.opinion,
                "reason": item.reason,
                "model": request["model"],
                "prompt_version": request["prompt_version"],
            }
            for item in validated
        ]
        inserted = connection.execute(
            pg_insert(h2.OPINIONS)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[h2.OPINIONS.c.workspace_id, h2.OPINIONS.c.recon_line_result_id]
            )
            .returning(h2.OPINIONS.c.id)
        ).all()
        return "applied" if inserted else "discarded_stale"


__all__ = ["ItemOpinionHandler"]
