"""Shared, side-effect-free input and result types for document parsers."""

from __future__ import annotations

from pydantic import Field

from reconbench_core.normalize import DEFAULT_CHARGE_KEYWORDS
from reconbench_core.schema import DocType, Document, FileFormat, FrozenModel, WarnLevel


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


class ParseResult(FrozenModel):
    success: bool
    file_format: FileFormat | None = None
    documents: tuple[Document, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()
    layout_signature: str
    reason_code: str | None = None
    page_texts: tuple[PageText, ...] = ()
