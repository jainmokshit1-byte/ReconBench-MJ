"""No duplicate ingestion (accuracy plan PD-0b-4, decision-log D103 decision 9).

The rules reuse the existing identity pieces: canonical document numbers, the
reconciliation engine's invoice duplicate key (supplier, number, financial year) and its
look-alike number fold, and the AC-8b re-scan match found by ``_upsert_document``. No new
fuzzy matching. Withdrawn documents never count; the same number from a different
supplier is not a duplicate.

* PO / GRN: identity is the document type and canonical number of an active document of
  the same supplier; unchanged content is skipped and changed content is a new version.
* Invoice, ``duplicate``: same supplier GSTIN, same canonical number, same financial
  year, number read with confidence. The document is not created; a person may
  **Discard** (default) or **Replace with this file** (a new version of the original).
* Invoice, ``possible``: the vendor is not resolved, the number was read at low
  confidence or reads the same only after the look-alike fold, the supplier is the same
  PAN under another GSTIN, or the AC-8b re-scan signals matched. Held until a person
  chooses **Discard** or **Keep as new**.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.normalize import canonicalize_document_number, financial_year
from reconbench_core.recon.engine import _duplicate_number_key as duplicate_number_key
from reconbench_core.schema import DocType, Document
from sqlalchemy.engine import Connection, RowMapping

from reconbench_app.db.metadata import metadata
from reconbench_app.document_identity import is_verified_field

# Below the default extraction gate (0.70, LOW_CONFIDENCE_DATA) a number is "read at low
# confidence": a match on it is held as possible, never blocked as certain.
NUMBER_CONFIDENCE_FLOOR = Decimal("0.70")
_GSTIN = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    kind: str  # "duplicate" | "possible"
    reason: str
    original_id: UUID


def _gstin(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    return compact if _GSTIN.fullmatch(compact) else None


def _plain(field: object | None) -> object | None:
    return getattr(field, "value", None) if field is not None else None


def active_structured_by_number(
    connection: Connection,
    *,
    workspace_id: UUID,
    doc_type: DocType,
    canonical_doc_no: str,
    vendor_identity: str | None,
) -> RowMapping | None:
    """The one active PO/GRN with this number and supplier, locked, or None."""

    document = metadata.tables["document"]
    rows = list(
        connection.execute(
            sa.select(document)
            .where(
                document.c.workspace_id == workspace_id,
                document.c.doc_type == doc_type.value,
                document.c.canonical_doc_no == canonical_doc_no,
                document.c.withdrawn_at.is_(None),
                document.c.current_version_id.is_not(None),
                document.c.vendor_identity.is_not_distinct_from(vendor_identity),
            )
            .limit(2)
            .with_for_update()
        ).mappings()
    )
    return rows[0] if len(rows) == 1 else None


def find_invoice_duplicate(
    connection: Connection,
    *,
    workspace_id: UUID,
    document: Document,
    rescan_match: RowMapping | None,
    structured: bool = False,
) -> DuplicateMatch | None:
    """Match an incoming invoice against the active invoices of the workspace."""

    header = document.header
    doc_no = _plain(header.doc_no)
    doc_no_text = doc_no.strip() if isinstance(doc_no, str) else ""
    canonical = canonicalize_document_number(doc_no_text) if doc_no_text else ""
    folded = duplicate_number_key(doc_no_text) if doc_no_text else ""
    doc_date = _plain(header.doc_date)
    year = financial_year(doc_date) if isinstance(doc_date, date) else None
    gstin = _gstin(_plain(header.vendor_gstin))
    pan = gstin[2:12] if gstin else None
    confidence = getattr(header.doc_no, "confidence", None)
    low_confidence = not is_verified_field(header.doc_no, structured=structured) and (
        confidence is None or Decimal(str(confidence)) < NUMBER_CONFIDENCE_FLOOR
    )

    document_table = metadata.tables["document"]
    version = metadata.tables["document_version"]
    conditions = []
    if canonical:
        conditions.append(version.c.canonical_doc_no == canonical)
    if pan:
        conditions.append(version.c.vendor_pan == pan)
    candidates = (
        list(
            connection.execute(
                sa.select(
                    document_table.c.id,
                    version.c.doc_no,
                    version.c.canonical_doc_no,
                    version.c.doc_date,
                    version.c.vendor_gstin,
                    version.c.vendor_pan,
                )
                .select_from(
                    document_table.join(
                        version, version.c.id == document_table.c.current_version_id
                    )
                )
                .where(
                    document_table.c.workspace_id == workspace_id,
                    document_table.c.doc_type == DocType.INVOICE.value,
                    document_table.c.withdrawn_at.is_(None),
                    sa.or_(*conditions),
                )
                .order_by(document_table.c.first_seen_at, document_table.c.id)
            ).mappings()
        )
        if conditions
        else []
    )

    possible: DuplicateMatch | None = None
    for candidate in candidates:
        original_id = cast(UUID, candidate["id"])
        candidate_date = candidate["doc_date"]
        candidate_year = (
            financial_year(candidate_date) if isinstance(candidate_date, date) else None
        )
        same_year = year is not None and year == candidate_year
        year_unknown = year is None or candidate_year is None
        candidate_gstin = _gstin(candidate["vendor_gstin"])
        candidate_pan = cast(str | None, candidate["vendor_pan"]) or (
            candidate_gstin[2:12] if candidate_gstin else None
        )
        if canonical and candidate["canonical_doc_no"] == canonical:
            if pan and candidate_pan and pan != candidate_pan:
                continue  # the same number from another supplier is not a duplicate
            if gstin and candidate_gstin and gstin == candidate_gstin and same_year:
                if not low_confidence:
                    return DuplicateMatch("duplicate", "same_vendor_number_year", original_id)
                possible = possible or DuplicateMatch(
                    "possible", "number_low_confidence", original_id
                )
            elif pan and candidate_pan and same_year:
                possible = possible or DuplicateMatch(
                    "possible", "same_supplier_other_registration", original_id
                )
            elif same_year or year_unknown:
                possible = possible or DuplicateMatch(
                    "possible",
                    "vendor_not_resolved" if not (pan and candidate_pan) else "date_not_read",
                    original_id,
                )
        elif (
            folded
            and pan
            and candidate_pan == pan
            and same_year
            and duplicate_number_key(str(candidate["doc_no"] or "")) == folded
        ):
            possible = possible or DuplicateMatch(
                "possible", "number_read_differently", original_id
            )
    if possible is not None:
        return possible
    if rescan_match is not None:
        return DuplicateMatch("possible", "rescan_match", cast(UUID, rescan_match["id"]))
    return None


def hold_for(
    connection: Connection, *, workspace_id: UUID, file_id: UUID, locator: str
) -> RowMapping | None:
    table = metadata.tables["duplicate_hold"]
    return (
        connection.execute(
            sa.select(table).where(
                table.c.workspace_id == workspace_id,
                table.c.source_file_id == file_id,
                table.c.source_locator == locator,
            )
        )
        .mappings()
        .one_or_none()
    )


def doc_no_text(document: Document) -> str | None:
    value: Any = _plain(document.header.doc_no)
    return str(value)[:256] if value not in (None, "") else None


__all__ = [
    "NUMBER_CONFIDENCE_FLOOR",
    "DuplicateMatch",
    "active_structured_by_number",
    "doc_no_text",
    "find_invoice_duplicate",
    "hold_for",
]
