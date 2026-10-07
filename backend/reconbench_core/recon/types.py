"""Immutable input and configuration contracts for reconciliation."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from reconbench_core.align import AlignmentConfig, AlignmentWeights
from reconbench_core.schema import (
    DecimalValue,
    DocType,
    Document,
    FrozenModel,
    LinkLevel,
    LinkMethod,
    LinkState,
    ToleranceMode,
)


def _default_alignment() -> AlignmentConfig:
    return AlignmentConfig(
        weights=AlignmentWeights(
            desc="0.55",
            code="0.20",
            hsn="0.15",
            uom="0.10",
        ),
        item_aligned="0.80",
        item_review="0.60",
        item_single_line_explicit="0.50",
        spec_conflict_cap="0.60",
    )


class ToleranceValues(FrozenModel):
    """Percentage and absolute allowance for one financial check."""

    pct: DecimalValue = Field(ge=0)
    abs: DecimalValue = Field(ge=0)


class ToleranceRule(FrozenModel):
    """Global tolerance with optional vendor and item overrides.

    ``by_vendor_item`` keys are ``"<vendor uuid>|<item key>"``; resolution is
    vendor-scoped item, then item, then vendor, then global (TR-REC-11).
    """

    pct: DecimalValue = Field(ge=0)
    abs: DecimalValue = Field(ge=0)
    by_vendor: dict[str, ToleranceValues] = Field(default_factory=dict)
    by_item: dict[str, ToleranceValues] = Field(default_factory=dict)
    by_vendor_item: dict[str, ToleranceValues] = Field(default_factory=dict)


def _default_tolerances() -> dict[str, ToleranceRule]:
    return {
        "qty_vs_grn": ToleranceRule(pct=0, abs=0),
        "cum_qty_vs_po": ToleranceRule(pct=0, abs=0),
        "unit_rate": ToleranceRule(pct="0.5", abs=1),
        "total_vs_expected": ToleranceRule(pct=1, abs=10),
        "arithmetic": ToleranceRule(pct=0, abs=1),
        "cum_value_vs_po": ToleranceRule(pct=0, abs=0),
    }


class ReconciliationConfig(FrozenModel):
    """Engine-only settings with the user-selected P0 defaults."""

    tolerances: dict[str, ToleranceRule] = Field(default_factory=_default_tolerances)
    limit_mode: ToleranceMode = ToleranceMode.OR
    extraction_gate: DecimalValue = Field(default=Decimal("0.70"), ge=0, le=1)
    link_auto_accept: DecimalValue = Field(default=Decimal("0.95"), ge=0, le=1)
    pending_ageing_days: int = Field(default=30, ge=0)
    duplicate_suspect_days: int = Field(default=3, ge=0)
    duplicate_suspect_edit_distance: int = Field(default=2, ge=0)
    payable_rate_mode: Literal["pay_at_po_rate", "hold"] = "pay_at_po_rate"
    date_order_level: Literal["fail", "warn"] = "fail"
    tax_rates: tuple[DecimalValue, ...] = (
        Decimal("5"),
        Decimal("12"),
        Decimal("18"),
        Decimal("28"),
    )
    buyer_entities: tuple[str, ...] = ()
    alignment: AlignmentConfig = Field(default_factory=_default_alignment)
    item_unit_aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_tax_rates(self) -> Self:
        if len(self.tax_rates) != len(set(self.tax_rates)):
            raise ValueError("tax rates must be unique")
        return self


class ReconciliationDocument(FrozenModel):
    """A versioned canonical source document in one evaluation snapshot."""

    document_id: UUID
    version_id: UUID
    vendor_id: UUID | None = None
    natural_key: str = ""
    document: Document


class ReconciliationLink(FrozenModel):
    """A current invoice/GRN-to-PO link frozen into the evaluation snapshot."""

    link_id: UUID
    from_document_id: UUID
    to_document_id: UUID
    method: LinkMethod
    state: LinkState
    confidence: DecimalValue = Field(ge=0, le=1)
    level: LinkLevel = LinkLevel.HEADER
    line_no: int | None = Field(default=None, ge=1)


class OpeningBalance(FrozenModel):
    """Opening accepted and billed quantities for one PO line."""

    opening_balance_id: UUID
    po_document_id: UUID
    po_line_no: int = Field(ge=1)
    received_qty: DecimalValue = Field(default=Decimal("0"), ge=0)
    billed_qty: DecimalValue = Field(default=Decimal("0"), ge=0)


class ReconciliationSnapshot(FrozenModel):
    """Read-only collection of documents, links and opening ledger events."""

    documents: tuple[ReconciliationDocument, ...]
    links: tuple[ReconciliationLink, ...] = ()
    opening_balances: tuple[OpeningBalance, ...] = ()
    vendor_pan_by_id: dict[UUID, str] = Field(default_factory=dict)
    rejected_invoice_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def _unique_ids_and_valid_references(self) -> Self:
        document_ids = [item.document_id for item in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("snapshot document IDs must be unique")
        known = set(document_ids)
        if any(
            link.from_document_id not in known or link.to_document_id not in known
            for link in self.links
        ):
            raise ValueError("snapshot links must reference documents in the snapshot")
        documents_by_id = {item.document_id: item.document for item in self.documents}
        if any(
            documents_by_id[link.to_document_id].header.doc_type is not DocType.PO
            or documents_by_id[link.from_document_id].header.doc_type
            not in {DocType.INVOICE, DocType.GRN}
            for link in self.links
        ):
            raise ValueError("snapshot links must connect an invoice or GRN to a PO")
        if any(
            item.po_document_id not in known
            or documents_by_id[item.po_document_id].header.doc_type is not DocType.PO
            for item in self.opening_balances
        ):
            raise ValueError("opening balances must reference a PO in the snapshot")
        link_ids = [item.link_id for item in self.links]
        if len(link_ids) != len(set(link_ids)):
            raise ValueError("snapshot link IDs must be unique")
        opening_ids = [item.opening_balance_id for item in self.opening_balances]
        if len(opening_ids) != len(set(opening_ids)):
            raise ValueError("opening balance IDs must be unique")
        if not set(self.rejected_invoice_ids).issubset(
            {
                item.document_id
                for item in self.documents
                if item.document.header.doc_type is DocType.INVOICE
            }
        ):
            raise ValueError("rejected IDs must reference invoices in the snapshot")
        return self
