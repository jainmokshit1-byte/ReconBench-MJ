"""H2 uncertain-item opinion: which run lines qualify, the text they may send, and their state.

Shared by the producer (a succeeded reconciliation run), the AI worker and the
case API. A line qualifies when the rules marked it ``unsure`` with a score in
``[0.60, 0.80)`` and the core spec-token check finds no conflict (16GB vs 8GB is
never put to the model). Only descriptions, item codes, HSN/SAC and units are
sent, with contact, banking and tax identifiers replaced by placeholders. The
answer is an advisory row in ``ai_line_opinion``; run rows are never changed.

One request covers a contiguous slice of a run's qualifying lines, ordered by
invoice line then PO line so equal inputs in a later run produce the same
payload (and hit the cache). ``subject_version`` is ``start:count:fingerprint``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.align import spec_tokens_conflict
from reconbench_core.normalize import normalize_item_text
from reconbench_core.schema import RunStatus
from sqlalchemy.engine import Connection

from reconbench_app.ai.prompts import H2PairText
from reconbench_app.ai.redaction import STRICT_CATEGORIES, Category, redact
from reconbench_app.db.metadata import metadata

HOOK = "h2_item"
SUBJECT_TYPE = "recon_line_batch"
SKIP_SOURCE_CHANGED = "SOURCE_CHANGED"
BAND_LOW = Decimal("0.60")
BAND_HIGH = Decimal("0.80")
MAX_PAIRS = 20
MAX_DESCRIPTION = 300

NOT_ELIGIBLE = "NOT_ELIGIBLE"
OUTSIDE_BAND = "OUTSIDE_BAND"
SPEC_CONFLICT = "SPEC_CONFLICT"
NOT_REQUESTED = "NOT_REQUESTED"
NO_OPINION = "NO_OPINION"

RUNS = metadata.tables["recon_run"]
LINE_RESULTS = metadata.tables["recon_line_result"]
DOC_LINES = metadata.tables["doc_line"]
OPINIONS = metadata.tables["ai_line_opinion"]
REQUESTS = metadata.tables["ai_request"]

OpinionState = Literal["pending", "available", "not_applicable", "unavailable"]


@dataclass(frozen=True, slots=True)
class ItemText:
    description: str | None
    item_code: str | None
    hsn_sac: str | None
    uom: str | None

    def render(self) -> str:
        description = (self.description or "").replace("\n", " ").strip()
        parts = [f"Description: {description[:MAX_DESCRIPTION] or '(none)'}"]
        for label, value in (
            ("Item code", self.item_code),
            ("HSN/SAC", self.hsn_sac),
            ("Unit", self.uom),
        ):
            if value and value.strip():
                parts.append(f"{label}: {value.strip()}")
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class LineCandidate:
    """An ``unsure`` goods line of a run with its PO line; ``code`` is ``None`` when eligible."""

    line_result_id: UUID
    invoice_line_id: UUID
    po_doc_line_id: UUID
    score: Decimal | None
    invoice: ItemText
    po: ItemText
    code: str | None


@dataclass(frozen=True, slots=True)
class PairBatch:
    start: int
    lines: tuple[LineCandidate, ...]
    pairs: tuple[H2PairText, ...]
    fingerprint: str
    redacted: bool

    @property
    def subject_version(self) -> str:
        return f"{self.start}:{len(self.lines)}:{self.fingerprint}"

    @property
    def line_by_pair(self) -> dict[str, LineCandidate]:
        return {pair.pair_id: line for pair, line in zip(self.pairs, self.lines, strict=True)}


def in_band(score: Decimal | None) -> bool:
    return score is not None and BAND_LOW <= score < BAND_HIGH


def spec_conflict(invoice_description: str | None, po_description: str | None) -> bool:
    """The core alignment rule: both sides state different values for the same spec."""

    return spec_tokens_conflict(
        normalize_item_text(invoice_description or ""),
        normalize_item_text(po_description or ""),
    )


def ineligible_code(
    score: Decimal | None, invoice_description: str | None, po_description: str | None
) -> str | None:
    if not in_band(score):
        return OUTSIDE_BAND
    if spec_conflict(invoice_description, po_description):
        return SPEC_CONFLICT
    return None


def run_succeeded(connection: Connection, workspace_id: UUID, run_id: UUID) -> bool:
    status = connection.scalar(
        sa.select(RUNS.c.status).where(RUNS.c.workspace_id == workspace_id, RUNS.c.id == run_id)
    )
    return status == RunStatus.SUCCEEDED.value


def unsure_lines(connection: Connection, workspace_id: UUID, run_id: UUID) -> list[LineCandidate]:
    """Every ``unsure`` goods line of the run, classified, in the stable batching order."""

    invoice_line = DOC_LINES.alias("invoice_line")
    po_line = DOC_LINES.alias("po_line")
    rows = connection.execute(
        sa.select(
            LINE_RESULTS.c.id,
            LINE_RESULTS.c.invoice_line_id,
            LINE_RESULTS.c.po_doc_line_id,
            LINE_RESULTS.c.alignment_score,
            *(
                column.label(f"invoice_{column.name}")
                for column in (
                    invoice_line.c.description,
                    invoice_line.c.item_code,
                    invoice_line.c.hsn_sac,
                    invoice_line.c.uom,
                )
            ),
            *(
                column.label(f"po_{column.name}")
                for column in (
                    po_line.c.description,
                    po_line.c.item_code,
                    po_line.c.hsn_sac,
                    po_line.c.uom,
                )
            ),
        )
        .select_from(
            LINE_RESULTS.join(
                invoice_line,
                sa.and_(
                    invoice_line.c.workspace_id == LINE_RESULTS.c.workspace_id,
                    invoice_line.c.id == LINE_RESULTS.c.invoice_line_id,
                ),
            ).join(
                po_line,
                sa.and_(
                    po_line.c.workspace_id == LINE_RESULTS.c.workspace_id,
                    po_line.c.id == LINE_RESULTS.c.po_doc_line_id,
                ),
            )
        )
        .where(
            LINE_RESULTS.c.workspace_id == workspace_id,
            LINE_RESULTS.c.run_id == run_id,
            LINE_RESULTS.c.alignment_state == "unsure",
            LINE_RESULTS.c.line_type == "goods",
            LINE_RESULTS.c.po_doc_line_id.is_not(None),
        )
        .order_by(LINE_RESULTS.c.invoice_line_id, LINE_RESULTS.c.po_doc_line_id, LINE_RESULTS.c.id)
    ).mappings()
    candidates: list[LineCandidate] = []
    for row in rows:
        score = row["alignment_score"]
        candidates.append(
            LineCandidate(
                line_result_id=UUID(str(row["id"])),
                invoice_line_id=UUID(str(row["invoice_line_id"])),
                po_doc_line_id=UUID(str(row["po_doc_line_id"])),
                score=Decimal(str(score)) if score is not None else None,
                invoice=ItemText(
                    row["invoice_description"],
                    row["invoice_item_code"],
                    row["invoice_hsn_sac"],
                    row["invoice_uom"],
                ),
                po=ItemText(
                    row["po_description"], row["po_item_code"], row["po_hsn_sac"], row["po_uom"]
                ),
                code=ineligible_code(score, row["invoice_description"], row["po_description"]),
            )
        )
    return candidates


def eligible_lines(connection: Connection, workspace_id: UUID, run_id: UUID) -> list[LineCandidate]:
    """Qualifying lines of a succeeded run; empty for a run that has not succeeded."""

    if not run_succeeded(connection, workspace_id, run_id):
        return []
    return [line for line in unsure_lines(connection, workspace_id, run_id) if line.code is None]


def build_batch(lines: Sequence[LineCandidate], start: int, count: int) -> PairBatch | None:
    selected = tuple(lines[start : start + count])
    if start < 0 or count < 1 or len(selected) != count:
        return None
    numbering: dict[tuple[Category, str], str] = {}
    pairs: list[H2PairText] = []
    redacted = False
    for index, line in enumerate(selected, start=1):
        invoice = redact(line.invoice.render(), STRICT_CATEGORIES, numbering=numbering)
        po = redact(line.po.render(), STRICT_CATEGORIES, numbering=numbering)
        redacted = redacted or bool(invoice.spans or po.spans)
        pairs.append(H2PairText(f"p{index}", invoice.text, po.text))
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                {
                    "line_result_id": str(line.line_result_id),
                    "invoice": line.invoice.render(),
                    "po": line.po.render(),
                }
                for line in selected
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PairBatch(start, selected, tuple(pairs), fingerprint, redacted)


def batches(lines: Sequence[LineCandidate], max_pairs: int) -> list[PairBatch]:
    size = max(1, min(max_pairs, MAX_PAIRS))
    built = (
        build_batch(lines, start, min(size, len(lines) - start))
        for start in range(0, len(lines), size)
    )
    return [batch for batch in built if batch is not None]


def parse_subject_version(value: str) -> tuple[int, int, str] | None:
    parts = value.split(":")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return int(parts[0]), int(parts[1]), parts[2]


def load_batch(
    connection: Connection, workspace_id: UUID, run_id: UUID, subject_version: str
) -> PairBatch | None:
    """The batch a request was queued with, or ``None`` when the run's lines no longer match."""

    parsed = parse_subject_version(subject_version)
    if parsed is None:
        return None
    start, count, fingerprint = parsed
    batch = build_batch(eligible_lines(connection, workspace_id, run_id), start, count)
    if batch is None or batch.fingerprint != fingerprint:
        return None
    return batch


def _unavailable_code(request: Any) -> str:
    outcome = request["apply_outcome"]
    return str(
        request["error_code"]
        or request["skip_code"]
        or {
            "discarded_stale": SKIP_SOURCE_CHANGED,
            "discarded_revoked": "AI_REVOKED",
            "applied": NO_OPINION,
            "not_applicable": NO_OPINION,
        }.get(str(outcome))
        or str(request["request_state"]).upper()
    )


def _request_state(request: Any) -> tuple[OpinionState, str | None]:
    state = request["request_state"]
    if state in {"queued", "reserved", "sending"}:
        return "pending", None
    return "unavailable", _unavailable_code(request)


def line_states(
    connection: Connection,
    workspace_id: UUID,
    run_id: UUID,
    line_result_ids: Collection[UUID],
) -> dict[UUID, dict[str, Any]]:
    """``pending | available | not_applicable | unavailable`` (with a code) per line result."""

    wanted = set(line_result_ids)
    states: dict[UUID, dict[str, Any]] = {
        line_id: {"state": "not_applicable", "code": NOT_ELIGIBLE} for line_id in wanted
    }
    if not wanted:
        return states
    succeeded = run_succeeded(connection, workspace_id, run_id)
    candidates = unsure_lines(connection, workspace_id, run_id) if succeeded else []
    eligible = [line for line in candidates if line.code is None]
    for line in candidates:
        if line.line_result_id in wanted and line.code is not None:
            states[line.line_result_id] = {"state": "not_applicable", "code": line.code}
    position = {line.line_result_id: index for index, line in enumerate(eligible)}
    covering: dict[int, tuple[OpinionState, str | None]] = {}
    requests = connection.execute(
        sa.select(
            REQUESTS.c.subject_version,
            REQUESTS.c.request_state,
            REQUESTS.c.apply_outcome,
            REQUESTS.c.error_code,
            REQUESTS.c.skip_code,
        )
        .where(
            REQUESTS.c.workspace_id == workspace_id,
            REQUESTS.c.hook == HOOK,
            REQUESTS.c.run_id == run_id,
        )
        .order_by(REQUESTS.c.created_at, REQUESTS.c.attempt_no)
    ).mappings()
    for request in requests:
        parsed = parse_subject_version(str(request["subject_version"]))
        if parsed is None:
            continue
        start, count, _fingerprint = parsed
        for index in range(start, start + count):
            covering[index] = _request_state(request)
    for line_id in wanted & position.keys():
        state, code = covering.get(position[line_id], ("not_applicable", NOT_REQUESTED))
        states[line_id] = {"state": state, "code": code}
    for row in connection.execute(
        sa.select(
            OPINIONS.c.recon_line_result_id,
            OPINIONS.c.opinion,
            OPINIONS.c.reason,
            OPINIONS.c.model,
            OPINIONS.c.created_at,
        ).where(
            OPINIONS.c.workspace_id == workspace_id,
            OPINIONS.c.run_id == run_id,
            OPINIONS.c.recon_line_result_id.in_(wanted),
        )
    ).mappings():
        states[UUID(str(row["recon_line_result_id"]))] = {
            "state": "available",
            "code": None,
            "opinion": row["opinion"],
            "reason": row["reason"],
            "model": row["model"],
            "created_at": row["created_at"],
        }
    return states


__all__ = [
    "BAND_HIGH",
    "BAND_LOW",
    "HOOK",
    "SKIP_SOURCE_CHANGED",
    "SUBJECT_TYPE",
    "LineCandidate",
    "PairBatch",
    "batches",
    "eligible_lines",
    "in_band",
    "ineligible_code",
    "line_states",
    "load_batch",
    "spec_conflict",
]
