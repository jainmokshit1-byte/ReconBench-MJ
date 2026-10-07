"""H3 top-five PO re-rank: the subject a request refers to and the text it may send.

Shared by the producer (link resolution) and the AI worker. An unlinked invoice
that cites no PO reference may ask which of its same-vendor top-five content
candidates it most likely belongs to. Only document numbers, dates and line
items are sent; GSTINs, PANs and contact or banking details are replaced by
placeholders. The answer becomes at most one ``suggested`` link that a person
confirms or rejects; it never feeds balances or verdicts while it is pending.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.mapping.links import (
    MappingConfig,
    MappingDocument,
    MappingPO,
    rank_po_candidates,
)
from reconbench_core.schema import DocType, Document
from sqlalchemy.engine import Connection

from reconbench_app.ai.redaction import STRICT_CATEGORIES, Category, RedactedText, redact
from reconbench_app.db.metadata import metadata

HOOK = "h3_rerank"
SUBJECT_TYPE = "link_resolution"
SKIP_SOURCE_CHANGED = "SOURCE_CHANGED"
POOL_SIZE = 5
MAX_LINES = 40
MAX_DESCRIPTION = 200

RESOLUTIONS = metadata.tables["link_resolution"]
DOCUMENTS = metadata.tables["document"]
LINKS = metadata.tables["link"]
POINTERS = metadata.tables["link_current"]


@dataclass(frozen=True, slots=True)
class PoolCandidate:
    token: str
    po_document_id: UUID
    score: Decimal
    text: RedactedText


@dataclass(frozen=True, slots=True)
class RerankPool:
    fingerprint: str
    invoice: RedactedText
    candidates: tuple[PoolCandidate, ...]

    @property
    def redacted(self) -> bool:
        return bool(self.invoice.spans or any(item.text.spans for item in self.candidates))


@dataclass(frozen=True, slots=True)
class ResolutionState:
    resolution_id: UUID
    invoice_id: UUID
    input_hash: str
    open: bool


def _value(field: object | None) -> str | None:
    value = getattr(field, "value", None)
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(getattr(value, "value", value))


def _document_text(label: str, document: Document) -> str:
    header = document.header
    lines = [
        f"{label} number: {_value(header.doc_no) or '(none)'}",
        f"Date: {_value(header.doc_date) or '(none)'}",
        "Lines:",
    ]
    for line in sorted(document.lines, key=lambda item: item.line_no)[:MAX_LINES]:
        description = (_value(line.description) or "").replace("\n", " ")[:MAX_DESCRIPTION]
        parts = [description or "(no description)"]
        for name, field in (
            ("item code", line.item_code),
            ("HSN/SAC", line.hsn_sac),
            ("qty", line.quantity),
            ("uom", line.uom),
            ("rate", line.rate),
            ("amount", line.amount),
        ):
            value = _value(field)
            if value:
                parts.append(f"{name} {value}")
        lines.append(f"{line.line_no}. " + "; ".join(parts))
    if len(document.lines) > MAX_LINES:
        lines.append(f"({len(document.lines) - MAX_LINES} more lines not shown)")
    return "\n".join(lines)


def build_pool(
    source: MappingDocument,
    purchase_orders: Sequence[MappingPO],
    config: MappingConfig,
) -> RerankPool | None:
    """The same-vendor top five by content score, or ``None`` when there is no candidate."""

    if source.document.header.doc_type is not DocType.INVOICE:
        return None
    ranked = rank_po_candidates(source, tuple(purchase_orders), config, limit=POOL_SIZE)
    if not ranked:
        return None
    by_id = {po.document_id: po for po in purchase_orders}
    numbering: dict[tuple[Category, str], str] = {}
    invoice = redact(
        _document_text("Invoice", source.document),
        STRICT_CATEGORIES,
        numbering=numbering,
    )
    candidates = tuple(
        PoolCandidate(
            token=f"c{index}",
            po_document_id=candidate.po_document_id,
            score=candidate.score,
            text=redact(
                _document_text("PO", by_id[candidate.po_document_id].document),
                STRICT_CATEGORIES,
                numbering=numbering,
            ),
        )
        for index, candidate in enumerate(ranked, start=1)
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "source": source.model_dump(mode="json"),
                "candidates": [
                    {
                        "score": format(candidate.score, "f"),
                        "po": by_id[candidate.po_document_id].model_dump(mode="json"),
                    }
                    for candidate in ranked
                ],
                "config": config.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return RerankPool(fingerprint=fingerprint, invoice=invoice, candidates=candidates)


def _live_link_exists(workspace_id: UUID, invoice_column: sa.ColumnElement[UUID]) -> sa.Exists:
    return sa.exists(
        sa.select(1)
        .select_from(
            POINTERS.join(
                LINKS,
                sa.and_(
                    POINTERS.c.workspace_id == LINKS.c.workspace_id,
                    POINTERS.c.current_link_id == LINKS.c.id,
                ),
            )
        )
        .where(
            POINTERS.c.workspace_id == workspace_id,
            POINTERS.c.link_type == "inv_po",
            POINTERS.c.from_document_id == invoice_column,
            LINKS.c.state != "rejected",
        )
    )


def _resolution_query(workspace_id: UUID) -> sa.Select[tuple[object, ...]]:
    return (
        sa.select(
            RESOLUTIONS.c.id,
            RESOLUTIONS.c.document_id,
            RESOLUTIONS.c.input_hash,
            sa.and_(
                RESOLUTIONS.c.outcome == "unlinked",
                RESOLUTIONS.c.cited_refs == sa.text("'{}'::text[]"),
                DOCUMENTS.c.doc_type == DocType.INVOICE.value,
                DOCUMENTS.c.withdrawn_at.is_(None),
                sa.not_(_live_link_exists(workspace_id, RESOLUTIONS.c.document_id)),
            ).label("open"),
        )
        .select_from(
            RESOLUTIONS.join(
                DOCUMENTS,
                sa.and_(
                    DOCUMENTS.c.workspace_id == RESOLUTIONS.c.workspace_id,
                    DOCUMENTS.c.id == RESOLUTIONS.c.document_id,
                ),
            )
        )
        .where(RESOLUTIONS.c.workspace_id == workspace_id)
    )


def open_resolutions(connection: Connection, workspace_id: UUID) -> list[ResolutionState]:
    """Unlinked invoices that cite no PO and have no live link, oldest resolution first."""

    query = _resolution_query(workspace_id).order_by(RESOLUTIONS.c.computed_at, RESOLUTIONS.c.id)
    return [
        ResolutionState(
            resolution_id=UUID(str(row.id)),
            invoice_id=UUID(str(row.document_id)),
            input_hash=str(row.input_hash),
            open=True,
        )
        for row in connection.execute(query)
        if row.open
    ]


def load_resolution(
    connection: Connection,
    workspace_id: UUID,
    resolution_id: UUID,
    *,
    for_update: bool = False,
) -> ResolutionState | None:
    query = _resolution_query(workspace_id).where(RESOLUTIONS.c.id == resolution_id)
    if for_update:
        query = query.with_for_update(of=RESOLUTIONS)
    row = connection.execute(query).first()
    if row is None:
        return None
    return ResolutionState(
        resolution_id=resolution_id,
        invoice_id=UUID(str(row.document_id)),
        input_hash=str(row.input_hash),
        open=bool(row.open),
    )


__all__ = [
    "HOOK",
    "POOL_SIZE",
    "SKIP_SOURCE_CHANGED",
    "SUBJECT_TYPE",
    "PoolCandidate",
    "RerankPool",
    "ResolutionState",
    "build_pool",
    "load_resolution",
    "open_resolutions",
]
