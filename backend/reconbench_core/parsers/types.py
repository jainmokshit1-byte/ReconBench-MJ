"""Shared, side-effect-free input and result types for document parsers."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from reconbench_core.normalize import DEFAULT_CHARGE_KEYWORDS
from reconbench_core.schema import DocType, Document, FileFormat, FrozenModel, WarnLevel


class BuyerEntityHint(FrozenModel):
    """One of the workspace's own legal entities (config ``buyer_entities``)."""

    gstin: str
    name: str = ""


# Total GST rates (CGST + SGST, or IGST) in percent, used when config has no slabs.
DEFAULT_GST_RATES: tuple[Decimal, ...] = tuple(
    Decimal(rate) for rate in ("0.25", "3", "5", "12", "18", "28", "40")
)


class PageSplit(StrEnum):
    """How a PDF's pages become documents (accuracy plan AC-3, owner decision D-9)."""

    # Split by page titles; a split or a type other than the zone waits for a person.
    AUTO = "auto"
    # A person accepted the proposed split.
    CONFIRMED = "confirmed"
    # A person kept the file as one document of the upload zone's type.
    OFF = "off"


class ParserHints(FrozenModel):
    """Upload-zone and saved-layout hints passed into the parser boundary."""

    doc_type: DocType | None = None
    file_format: FileFormat | None = None
    profile: str | None = None
    synonyms: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    column_mapping: dict[str, str] = Field(default_factory=dict)
    custom_xpaths: dict[str, str] = Field(default_factory=dict)
    uom_aliases: dict[str, str] = Field(default_factory=dict)
    charge_keywords: tuple[str, ...] = DEFAULT_CHARGE_KEYWORDS
    max_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    max_pages: int = Field(default=100, ge=1)
    # The buyer's own identity is never read as the supplier (accuracy plan AC-1).
    buyer_entities: tuple[BuyerEntityHint, ...] = ()
    gst_rates: tuple[Decimal, ...] = DEFAULT_GST_RATES
    page_split: PageSplit = PageSplit.AUTO


class ParseWarning(FrozenModel):
    code: str
    message: str
    level: WarnLevel = WarnLevel.WARNING
    locator: str | None = None


class PageText(FrozenModel):
    """Text extracted from one source page for indexing and review."""

    page: int = Field(ge=1)
    content: str
    scanned: bool = False
    is_ocr: bool = False


class PageImageHash(FrozenModel):
    """Perceptual fingerprint of one source PDF page, kept without image bytes."""

    page: int = Field(ge=1)
    value: str = Field(pattern=r"^[0-9a-f]{16}$")


class ParseResult(FrozenModel):
    success: bool
    file_format: FileFormat | None = None
    documents: tuple[Document, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()
    layout_signature: str
    reason_code: str | None = None
    page_texts: tuple[PageText, ...] = ()
    page_image_hashes: tuple[PageImageHash, ...] = ()
