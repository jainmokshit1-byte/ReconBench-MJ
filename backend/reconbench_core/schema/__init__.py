"""Canonical frozen document and reconciliation models for the pure core."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)


def _reject_float(value: object) -> object:
    """Prevent binary floating-point values entering canonical numeric fields."""

    if isinstance(value, float):
        raise ValueError("binary floating-point values are not allowed; use Decimal or text")
    return value


DecimalValue = Annotated[Decimal, BeforeValidator(_reject_float)]
Probability = Annotated[DecimalValue, Field(ge=Decimal("0"), le=Decimal("1"))]
HashHex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FrozenModel(BaseModel):
    """Base for immutable, strict canonical data models."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class DocType(StrEnum):
    PO = "po"
    GRN = "grn"
    INVOICE = "invoice"


class UploadZone(StrEnum):
    PO = "po"
    GRN = "grn"
    INVOICE = "invoice"
    OPENING_BALANCE = "opening_balance"


class FileFormat(StrEnum):
    XML = "xml"
    CSV = "csv"
    PDF = "pdf"
    ZIP = "zip"


class FileStatus(StrEnum):
    STAGED = "staged"
    QUEUED = "queued"
    PARSING = "parsing"
    PARSED = "parsed"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_PASSWORD = "needs_password"
    REMOVED = "removed"


class MismatchState(StrEnum):
    NONE = "none"
    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    KEPT = "kept"


class POStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class LineType(StrEnum):
    GOODS = "goods"
    CHARGE = "charge"


class FieldSource(StrEnum):
    RULE = "rule"
    AI = "ai"
    MANUAL = "manual"


class FieldOrigin(StrEnum):
    """How a field value entered the document model, independent of verification."""

    OCR = "ocr"
    MANUAL = "manual"
    ERP_IMPORT = "erp_import"


class FieldVerification(StrEnum):
    """Whether a person or a complete deterministic system check verified a value."""

    NONE = "none"
    USER = "user"
    SYSTEM_CHECK = "system_check"


class ExtractionMethod(StrEnum):
    XML_PROFILE = "xml_profile"
    CSV_MAPPING = "csv_mapping"
    PDF_TEXT = "pdf_text"
    PDF_OCR = "pdf_ocr"
    AI_FALLBACK = "ai_fallback"
    AI_FIRST = "ai_first"
    MANUAL = "manual"
    SHELL = "shell"


class WarnLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class VendorRule(StrEnum):
    GSTIN = "gstin"
    PAN = "pan"
    NAME_EXACT = "name_exact"
    ALIAS = "alias"
    FUZZY = "fuzzy"
    MANUAL = "manual"
    NONE = "none"


class VendorAnchor(StrEnum):
    GSTIN = "gstin"
    PAN = "pan"
    NAME = "name"


class AliasSource(StrEnum):
    MERGE = "merge"
    MANUAL = "manual"
    LEARNED = "learned"
    SEED = "seed"


class SuggestionState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AIOpinion(StrEnum):
    SAME = "same"
    DIFFERENT = "different"
    UNSURE = "unsure"


class LinkType(StrEnum):
    INV_PO = "inv_po"
    GRN_PO = "grn_po"
    INV_GRN = "inv_grn"


class LinkLevel(StrEnum):
    HEADER = "header"
    LINE = "line"


class LinkMethod(StrEnum):
    LABELED = "labeled"
    REVERSE = "reverse"
    VIA_GRN = "via_grn"
    CONTENT = "content"
    MANUAL = "manual"


class LinkState(StrEnum):
    AUTO = "auto"
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ResolutionOutcome(StrEnum):
    LINKED = "linked"
    SUGGESTED = "suggested"
    UNLINKED = "unlinked"


class CaseStatus(StrEnum):
    DUPLICATE = "duplicate"
    UNLINKED = "unlinked"
    NEEDS_REVIEW = "needs_review"
    EXCEPTION = "exception"
    PENDING = "pending"
    MATCHED_FLAGS = "matched_flags"
    MATCHED = "matched"


class OverlayType(StrEnum):
    APPROVED = "approved"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"


class ActionType(StrEnum):
    CONFIRM_LINK = "confirm_link"
    CHANGE_LINK = "change_link"
    APPROVE = "approve"
    HOLD = "hold"
    REJECT = "reject"
    NOTE = "note"
    RELEASE = "release"


class ReasonLevel(StrEnum):
    BLOCK = "block"
    REVIEW = "review"
    FAIL = "fail"
    PENDING = "pending"
    WARN = "warn"
    INFO = "info"


class AlignmentState(StrEnum):
    ALIGNED = "aligned"
    UNSURE = "unsure"
    NOT_ON_PO = "not_on_po"
    CHARGE = "charge"
    UNLINKED = "unlinked"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunTrigger(StrEnum):
    USER = "user"
    AUTO_PENDING = "auto_pending"
    MANUAL_RECON = "manual_recon"


class ExportStatus(StrEnum):
    QUEUED = "queued"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class AIMode(StrEnum):
    RULES_ONLY = "rules_only"
    AI_ASSISTED = "ai_assisted"
    AI_FIRST = "ai_first"


class AIProvider(StrEnum):
    CLAUDE = "claude"
    OPENAI = "openai"
    GEMINI = "gemini"


class AIKeyStorage(StrEnum):
    SESSION = "session"
    SAVED = "saved"


class AIHook(StrEnum):
    H1_EXTRACT = "h1_extract"
    H2_ITEM = "h2_item"
    H3_RERANK = "h3_rerank"
    H4_VENDOR = "h4_vendor"
    H5_EXPLAIN = "h5_explain"


class AIOutcome(StrEnum):
    OK = "ok"
    CACHE_HIT = "cache_hit"
    INVALID_OUTPUT = "invalid_output"
    UNGROUNDED = "ungrounded"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    BAD_KEY = "bad_key"
    BUDGET_STOPPED = "budget_stopped"
    ERROR = "error"


class UserRole(StrEnum):
    AP_EXECUTIVE = "ap_executive"
    MANAGER = "manager"
    AUDITOR = "auditor"


class ScopeType(StrEnum):
    VENDOR = "vendor"
    PO = "po"
    INVOICE = "invoice"


class ProcessingStage(StrEnum):
    RECEIVED = "received"
    SNIFFED = "sniffed"
    PARSED = "parsed"
    QA = "qa"
    GATED = "gated"
    UPSERTED = "upserted"
    VENDOR_RESOLVED = "vendor_resolved"
    LINKED = "linked"
    LEDGER_UPDATED = "ledger_updated"
    EVALUATED = "evaluated"


class StageOutcome(StrEnum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    SKIPPED = "skipped"


class ReasonCode(StrEnum):
    DUP_INVOICE = "DUP_INVOICE"
    NO_PO_LINK = "NO_PO_LINK"
    LINK_INFERRED = "LINK_INFERRED"
    LOW_CONFIDENCE_DATA = "LOW_CONFIDENCE_DATA"
    DUP_SUSPECTED = "DUP_SUSPECTED"
    ITEM_UNSURE = "ITEM_UNSURE"
    NO_TEXT_LAYER = "NO_TEXT_LAYER"
    VENDOR_MISMATCH = "VENDOR_MISMATCH"
    PO_NOT_OPEN = "PO_NOT_OPEN"
    ITEM_NOT_ON_PO = "ITEM_NOT_ON_PO"
    QTY_OVER_PO = "QTY_OVER_PO"
    QTY_OVER_GRN = "QTY_OVER_GRN"
    RATE_HIGH = "RATE_HIGH"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    MATH_ERROR = "MATH_ERROR"
    PO_VALUE_EXCEEDED = "PO_VALUE_EXCEEDED"
    CURRENCY_DIFF = "CURRENCY_DIFF"
    UOM_MISMATCH = "UOM_MISMATCH"
    DATE_ORDER = "DATE_ORDER"
    GRN_PENDING = "GRN_PENDING"
    RATE_LOW = "RATE_LOW"
    TAX_RATE_DIFF = "TAX_RATE_DIFF"
    TAX_TYPE_MISMATCH = "TAX_TYPE_MISMATCH"
    HSN_DIFF = "HSN_DIFF"
    GSTIN_INVALID = "GSTIN_INVALID"
    EXTRA_CHARGE = "EXTRA_CHARGE"
    GRN_OVER_PO = "GRN_OVER_PO"
    LATE_DELIVERY = "LATE_DELIVERY"


class CheckOutcome(StrEnum):
    PASS = "pass"
    INFO = "info"
    WARN = "warn"
    PENDING = "pending"
    REVIEW = "review"
    FAIL = "fail"
    BLOCK = "block"


class ToleranceMode(StrEnum):
    OR = "or"
    BOTH = "both"


class ToleranceSource(StrEnum):
    ITEM = "item"
    VENDOR = "vendor"
    GLOBAL = "global"


# Named exports keep reason-code use explicit at call sites while the enum stays
# the single source of truth for validation and JSON serialization.
DUP_INVOICE = ReasonCode.DUP_INVOICE
NO_PO_LINK = ReasonCode.NO_PO_LINK
LINK_INFERRED = ReasonCode.LINK_INFERRED
LOW_CONFIDENCE_DATA = ReasonCode.LOW_CONFIDENCE_DATA
DUP_SUSPECTED = ReasonCode.DUP_SUSPECTED
ITEM_UNSURE = ReasonCode.ITEM_UNSURE
NO_TEXT_LAYER = ReasonCode.NO_TEXT_LAYER
VENDOR_MISMATCH = ReasonCode.VENDOR_MISMATCH
PO_NOT_OPEN = ReasonCode.PO_NOT_OPEN
ITEM_NOT_ON_PO = ReasonCode.ITEM_NOT_ON_PO
QTY_OVER_PO = ReasonCode.QTY_OVER_PO
QTY_OVER_GRN = ReasonCode.QTY_OVER_GRN
RATE_HIGH = ReasonCode.RATE_HIGH
TOTAL_MISMATCH = ReasonCode.TOTAL_MISMATCH
MATH_ERROR = ReasonCode.MATH_ERROR
PO_VALUE_EXCEEDED = ReasonCode.PO_VALUE_EXCEEDED
CURRENCY_DIFF = ReasonCode.CURRENCY_DIFF
UOM_MISMATCH = ReasonCode.UOM_MISMATCH
DATE_ORDER = ReasonCode.DATE_ORDER
GRN_PENDING = ReasonCode.GRN_PENDING
RATE_LOW = ReasonCode.RATE_LOW
TAX_RATE_DIFF = ReasonCode.TAX_RATE_DIFF
TAX_TYPE_MISMATCH = ReasonCode.TAX_TYPE_MISMATCH
HSN_DIFF = ReasonCode.HSN_DIFF
GSTIN_INVALID = ReasonCode.GSTIN_INVALID
EXTRA_CHARGE = ReasonCode.EXTRA_CHARGE
GRN_OVER_PO = ReasonCode.GRN_OVER_PO
LATE_DELIVERY = ReasonCode.LATE_DELIVERY
REASON_CODES = tuple(ReasonCode)


class FieldEvidence(FrozenModel):
    """Where an extracted or corrected field can be checked in its source."""

    page: int | None = Field(default=None, ge=1)
    snippet: str | None = None
    bbox: tuple[DecimalValue, DecimalValue, DecimalValue, DecimalValue] | None = None


class FieldValue[ValueT](FrozenModel):
    """A canonical value together with confidence, provenance, and source evidence."""

    value: ValueT
    confidence: Probability | None = None
    source: FieldSource
    evidence: FieldEvidence
    origin: FieldOrigin | None = None
    verification: FieldVerification = FieldVerification.NONE
    verified_by: str | None = None
    verified_at: datetime | None = None


class DocumentHeader(FrozenModel):
    """Fields shared by purchase orders, goods receipts, and invoices."""

    doc_no: FieldValue[str] | None = None
    doc_date: FieldValue[date] | None = None
    vendor_name: FieldValue[str] | None = None
    vendor_gstin: FieldValue[str] | None = None
    buyer_gstin: FieldValue[str] | None = None
    currency: FieldValue[str] | None = None
    taxable_total: FieldValue[DecimalValue] | None = None
    cgst: FieldValue[DecimalValue] | None = None
    sgst: FieldValue[DecimalValue] | None = None
    igst: FieldValue[DecimalValue] | None = None
    round_off: FieldValue[DecimalValue] | None = None
    grand_total: FieldValue[DecimalValue] | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class POHeader(DocumentHeader):
    """Canonical purchase-order header. Missing fields remain representable."""

    doc_type: Literal[DocType.PO] = DocType.PO
    revision: FieldValue[str] | None = None
    status: FieldValue[POStatus] | None = None
    ship_to: FieldValue[str] | None = None
    payment_terms: FieldValue[str] | None = None
    delivery_date: FieldValue[date] | None = None


class GRNHeader(DocumentHeader):
    """Canonical goods-received-note header."""

    doc_type: Literal[DocType.GRN] = DocType.GRN
    po_refs: FieldValue[tuple[str, ...]] | None = None
    challan_no: FieldValue[str] | None = None
    supplier_invoice_no: FieldValue[str] | None = None


class InvoiceHeader(DocumentHeader):
    """Canonical supplier-invoice header."""

    doc_type: Literal[DocType.INVOICE] = DocType.INVOICE
    buyer_gstin: FieldValue[str] | None = None
    po_refs: FieldValue[tuple[str, ...]] | None = None
    grn_refs: FieldValue[tuple[str, ...]] | None = None
    challan_no: FieldValue[str] | None = None
    e_way_bill_no: FieldValue[str] | None = None
    irn: FieldValue[str] | None = None
    place_of_supply: FieldValue[str] | None = None


class DocLine(FrozenModel):
    """One source line with original evidence and normalized business values."""

    line_no: int = Field(ge=1)
    line_type: FieldValue[LineType] | None = None
    description: FieldValue[str] | None = None
    item_code: FieldValue[str] | None = None
    hsn_sac: FieldValue[str] | None = None
    uom: FieldValue[str] | None = None
    quantity: FieldValue[DecimalValue] | None = None
    received_qty: FieldValue[DecimalValue] | None = None
    rejected_qty: FieldValue[DecimalValue] | None = None
    accepted_qty: FieldValue[DecimalValue] | None = None
    rate: FieldValue[DecimalValue] | None = None
    discount: FieldValue[DecimalValue] | None = None
    amount: FieldValue[DecimalValue] | None = None
    tax_rate: FieldValue[DecimalValue] | None = None
    cgst_amount: FieldValue[DecimalValue] | None = None
    sgst_amount: FieldValue[DecimalValue] | None = None
    igst_amount: FieldValue[DecimalValue] | None = None
    po_ref: FieldValue[str] | None = None
    po_line_ref: FieldValue[str] | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class Document(FrozenModel):
    """A parsed document with a discriminated canonical header and its lines."""

    header: Annotated[POHeader | GRNHeader | InvoiceHeader, Field(discriminator="doc_type")]
    lines: tuple[DocLine, ...] = ()
    natural_key: str | None = None
    source_locator: str | None = None
    raw_text: str | None = None
    needs_verification: bool = False
    reason_codes: tuple[ReasonCode, ...] = ()

    @computed_field(return_type=Decimal | None)  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> Decimal | None:
        """Return the minimum available critical-field confidence for invoices."""

        if self.header.doc_type is not DocType.INVOICE:
            return None
        confidences: list[Decimal] = []
        for field in (self.header.doc_no, self.header.doc_date, self.header.grand_total):
            if field is None:
                return Decimal("0")
            if field.confidence is not None:
                confidences.append(field.confidence)
        vendor_confidences = [
            field.confidence
            for field in (self.header.vendor_name, self.header.vendor_gstin)
            if field is not None and field.confidence is not None
        ]
        if not vendor_confidences:
            return Decimal("0")
        confidences.append(max(vendor_confidences))
        line_confidences = [
            field.confidence
            for line in self.lines
            for field in (
                line.quantity,
                line.rate if line.rate is not None else line.amount,
            )
            if field is not None and field.confidence is not None
        ]
        if not line_confidences:
            return Decimal("0")
        confidences.extend(line_confidences)
        return min(confidences) if confidences else Decimal("0")


class ManifestDocument(FrozenModel):
    version_id: UUID
    vendor_id: UUID | None = None


class Manifest(FrozenModel):
    """Sorted, replayable list of all inputs that can affect one evaluation."""

    engine_version: str = Field(min_length=1)
    rules_hash: HashHex
    config_version_id: UUID
    run_date: date
    documents: tuple[ManifestDocument, ...] = ()
    links: tuple[UUID, ...] = ()
    opening_balances: tuple[UUID, ...] = ()
    rejected_invoices: tuple[UUID, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _sort_manifest_collections(cls, value: Any) -> Any:
        """Normalize collection order so equivalent snapshots hash identically."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "documents" in normalized:
            normalized["documents"] = sorted(normalized["documents"], key=_manifest_document_key)
        for name in ("links", "opening_balances", "rejected_invoices"):
            if name in normalized:
                normalized[name] = sorted(normalized[name], key=str)
        return normalized

    def canonical_json(self) -> str:
        """Return the stable JSON payload hashed by the database manifest contract."""

        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _manifest_document_key(value: Any) -> str:
    if isinstance(value, ManifestDocument):
        return str(value.version_id)
    if isinstance(value, dict):
        return str(value.get("version_id", ""))
    return str(value)


class Tolerance(FrozenModel):
    pct: DecimalValue
    abs: DecimalValue
    mode: ToleranceMode
    source: ToleranceSource


class CheckResult(FrozenModel):
    check_id: str = Field(min_length=1)
    check_version: int = Field(ge=1)
    code: ReasonCode | None = None
    level: ReasonLevel | None = None
    result: CheckOutcome
    expected: DecimalValue | str | None = None
    actual: DecimalValue | str | None = None
    variance: DecimalValue | None = None
    variance_pct: DecimalValue | None = None
    tolerance: Tolerance | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class LinkSummary(FrozenModel):
    link_id: UUID
    po_document_id: UUID
    po_no: str
    method: LinkMethod
    confidence: Probability
    state: LinkState
    grn_nos: tuple[str, ...] = ()


class LineResult(FrozenModel):
    """Frozen per-line output, including alignment, ledger, and payable values."""

    engine_version: str = Field(min_length=1)
    rules_hash: HashHex
    invoice_line_id: UUID | None = None
    line_no: int = Field(ge=1)
    line_type: LineType = LineType.GOODS
    status: CaseStatus
    po_document_id: UUID | None = None
    po_line_no: int | None = Field(default=None, ge=1)
    po_doc_line_id: UUID | None = None
    alignment_score: Probability | None = None
    alignment_state: AlignmentState
    grn_document_ids: tuple[UUID, ...] = ()
    uom: str | None = None
    po_qty: DecimalValue | None = None
    po_cap: DecimalValue | None = None
    billed_before: DecimalValue | None = None
    billed_qty: DecimalValue | None = None
    grn_accepted_cum: DecimalValue | None = None
    grn_rejected_cum: DecimalValue | None = None
    eligible_qty: DecimalValue | None = None
    balance_after: DecimalValue | None = None
    shortfall_qty: DecimalValue | None = None
    po_rate: DecimalValue | None = None
    invoice_net_rate: DecimalValue | None = None
    payable_rate: DecimalValue | None = None
    line_taxable: DecimalValue | None = None
    payable_taxable: DecimalValue | None = None
    at_risk: DecimalValue | None = None
    reason_codes: tuple[ReasonCode, ...] = ()
    checks: tuple[CheckResult, ...] = ()


class CaseResult(FrozenModel):
    """Frozen per-invoice result emitted by the deterministic engine."""

    engine_version: str = Field(min_length=1)
    rules_hash: HashHex
    invoice_id: UUID | None = None
    invoice_version_id: UUID | None = None
    status: CaseStatus
    aged: bool = False
    pending_days: int | None = Field(default=None, ge=0)
    duplicate_of_id: UUID | None = None
    invoice_taxable: DecimalValue | None = None
    invoice_total: DecimalValue | None = None
    expected_taxable: DecimalValue | None = None
    variance: DecimalValue | None = None
    variance_pct: DecimalValue | None = None
    payable_taxable: DecimalValue = Decimal("0")
    at_risk: DecimalValue = Decimal("0")
    at_risk_gst: DecimalValue = Decimal("0")
    blocked_value: DecimalValue = Decimal("0")
    payment_due_date: date | None = None
    po_nos: tuple[str, ...] = ()
    grn_nos: tuple[str, ...] = ()
    link_summary: tuple[LinkSummary, ...] = ()
    reason_codes: tuple[ReasonCode, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    lines: tuple[LineResult, ...] = ()


DATABASE_ENUMS: dict[str, type[StrEnum]] = {
    "doc_type": DocType,
    "upload_zone": UploadZone,
    "file_format": FileFormat,
    "file_status": FileStatus,
    "mismatch_state": MismatchState,
    "po_status": POStatus,
    "line_type": LineType,
    "field_source": FieldSource,
    "extraction_method": ExtractionMethod,
    "warn_level": WarnLevel,
    "vendor_rule": VendorRule,
    "vendor_anchor": VendorAnchor,
    "alias_source": AliasSource,
    "suggestion_state": SuggestionState,
    "ai_opinion": AIOpinion,
    "link_type": LinkType,
    "link_level": LinkLevel,
    "link_method": LinkMethod,
    "link_state": LinkState,
    "resolution_outcome": ResolutionOutcome,
    "case_status": CaseStatus,
    "overlay_type": OverlayType,
    "action_type": ActionType,
    "reason_level": ReasonLevel,
    "alignment_state": AlignmentState,
    "run_status": RunStatus,
    "run_trigger": RunTrigger,
    "export_status": ExportStatus,
    "ai_mode": AIMode,
    "ai_provider": AIProvider,
    "ai_key_storage": AIKeyStorage,
    "ai_hook": AIHook,
    "ai_outcome": AIOutcome,
    "user_role": UserRole,
    "scope_type": ScopeType,
    "processing_stage": ProcessingStage,
    "stage_outcome": StageOutcome,
}


__all__ = [
    "AIHook",
    "AIKeyStorage",
    "AIMode",
    "AIOpinion",
    "AIOutcome",
    "AIProvider",
    "ActionType",
    "AliasSource",
    "AlignmentState",
    "CaseResult",
    "CaseStatus",
    "CheckOutcome",
    "CheckResult",
    "DATABASE_ENUMS",
    "DecimalValue",
    "DocLine",
    "DocType",
    "Document",
    "DocumentHeader",
    "ExportStatus",
    "ExtractionMethod",
    "FieldEvidence",
    "FieldSource",
    "FieldValue",
    "FileFormat",
    "FileStatus",
    "FrozenModel",
    "GRNHeader",
    "InvoiceHeader",
    "LineResult",
    "LineType",
    "LinkLevel",
    "LinkMethod",
    "LinkState",
    "LinkSummary",
    "LinkType",
    "Manifest",
    "ManifestDocument",
    "MismatchState",
    "OverlayType",
    "POHeader",
    "POStatus",
    "ProcessingStage",
    "REASON_CODES",
    "ReasonCode",
    "ReasonLevel",
    "ResolutionOutcome",
    "RunStatus",
    "RunTrigger",
    "ScopeType",
    "StageOutcome",
    "SuggestionState",
    "Tolerance",
    "ToleranceMode",
    "ToleranceSource",
    "UploadZone",
    "UserRole",
    "VendorAnchor",
    "VendorRule",
    "WarnLevel",
    "DUP_INVOICE",
    "NO_PO_LINK",
    "LINK_INFERRED",
    "LOW_CONFIDENCE_DATA",
    "DUP_SUSPECTED",
    "ITEM_UNSURE",
    "NO_TEXT_LAYER",
    "VENDOR_MISMATCH",
    "PO_NOT_OPEN",
    "ITEM_NOT_ON_PO",
    "QTY_OVER_PO",
    "QTY_OVER_GRN",
    "RATE_HIGH",
    "TOTAL_MISMATCH",
    "MATH_ERROR",
    "PO_VALUE_EXCEEDED",
    "CURRENCY_DIFF",
    "UOM_MISMATCH",
    "DATE_ORDER",
    "GRN_PENDING",
    "RATE_LOW",
    "TAX_RATE_DIFF",
    "TAX_TYPE_MISMATCH",
    "HSN_DIFF",
    "GSTIN_INVALID",
    "EXTRA_CHARGE",
    "GRN_OVER_PO",
    "LATE_DELIVERY",
]
