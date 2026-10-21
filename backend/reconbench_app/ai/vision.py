"""Vision reader hook (``h6_vision``, accuracy plan AC-5): subject, eligibility, page images.

Shared by the producer (general worker), the AI worker and the review view. The
subject is one immutable parser version of a PDF invoice, PO or GRN (decision
D-3 closes D13 for this reader). Its pages are rendered and masked in the PDF
sandbox (``extraction/vision_pages.py``) and kept in ``document_page_image``;
only the masked image bytes are sent, never the OCR text (the reader must stay
independent of OCR, plan §3.1). The answer is a suggestion only (decision D-2 is
AC-6's): nothing here writes to a document; a person applies a value through
the normal correction save.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.extraction.vision import (
    Box,
    SentPage,
    VisionAnnotation,
    VisionCheck,
    VisionField,
    VisionLine,
    VisionPageKind,
    VisionReading,
    VisionUnreadable,
    check_reading,
    merge_page_readings,
    vision_document,
)
from reconbench_core.parsers.parties import party_hints_from_config
from reconbench_core.parsers.types import BuyerEntityHint
from reconbench_core.schema import DocType, Document
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, Engine

from reconbench_app.ai.contracts import (
    DocTypeName,
    InvalidOutput,
    VisionOutput,
    parse_output,
)
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.prompts import PromptPayload, vision_prompt
from reconbench_app.ai.provider import ProviderImage
from reconbench_app.ai.redaction import IMAGE_MASK_VERSION
from reconbench_app.db.metadata import metadata
from reconbench_app.extraction.vision import (
    BAND_TOP,
    BAND_VERSION,
    VISION_RENDER,
    MediaResolution,
    VisionReaderInfo,
)

_LOGGER = logging.getLogger(__name__)

HOOK = "h6_vision"
SUBJECT_TYPE = "document_version"
WIRED_DOC_TYPES: frozenset[str] = frozenset({"invoice", "po", "grn"})
# The existing correction endpoint accepts manual saves for each wired document type.
CORRECTABLE_DOC_TYPES: frozenset[str] = WIRED_DOC_TYPES
PDF_METHODS = frozenset({"pdf_text", "pdf_ocr"})

SKIP_SOURCE_CHANGED = "SOURCE_CHANGED"
SKIP_NOT_ELIGIBLE = "NOT_ELIGIBLE"
SKIP_PAGES_MISSING = "VISION_PAGES_MISSING"
SKIP_PAGES_FAILED = "VISION_PAGES_FAILED"
SKIP_READER_OFF = "VISION_READER_OFF"

SUGGESTION_LABELS: dict[str, str] = {
    "ai_assisted": "AI page-image reading: documents the rules could not verify",
    "ai_first": "AI page-image reading: requested for every PDF; rules output stays authoritative",
}

DOCUMENTS = metadata.tables["document"]
VERSIONS = metadata.tables["document_version"]
TEXTS = metadata.tables["document_text"]
LINES = metadata.tables["doc_line"]
SOURCES = metadata.tables["source_file"]
PAGES = metadata.tables["document_page_image"]
REQUESTS = metadata.tables["ai_request"]
CONSENSUS_RUNS = metadata.tables["consensus_run"]
CONFIG_ACTIVE = metadata.tables["config_active"]
CONFIG_VERSIONS = metadata.tables["config_version"]

VisionSkipReason = Literal["MODE_OFF", "NOT_PDF", "NOT_NEEDED", "TOO_MANY_PAGES", "NO_PAGES"]
ReaderPass = Literal["full", "crop:header", "crop:table", "crop:totals"]
BoxOrder = Literal["yx", "xy"]


def reader_params(reader: VisionReaderInfo, *, reader_pass: ReaderPass = "full") -> dict[str, Any]:
    """What the reader saw and how it was asked (WI-BE5): stored on ``ai_request`` and
    hashed into the vision cache key, so an answer is reused only for the same settings."""

    params: dict[str, Any] = {
        "jpeg_quality": VISION_RENDER.jpeg_quality,
        "mask_version": IMAGE_MASK_VERSION,
        "max_pixels": reader.max_pixels,
        "max_side": VISION_RENDER.max_side,
        "media_resolution": reader.media_resolution,
        "pass": reader_pass,
        "prompt_variant": reader.prompt_variant,
        "reader": reader.name,
        "render_version": VISION_RENDER.version,
        "sampling_profile": reader.sampling_profile,
        "thinking": reader.thinking,
    }
    if reader.band_prompt_variant is not None:
        # Only readers with the band read carry the key: every other key is unchanged.
        params["band"] = {
            "prompt_variant": reader.band_prompt_variant,
            "top": BAND_TOP,
            "version": BAND_VERSION,
        }
    return params


# --------------------------------------------------------------------------- eligibility


@dataclass(frozen=True, slots=True)
class VisionFacts:
    doc_type: str
    file_format: str
    extraction_method: str | None
    needs_verification: bool
    page_count: int


@dataclass(frozen=True, slots=True)
class VisionEligibility:
    eligible: bool
    reason: VisionSkipReason | None = None


def vision_eligibility(facts: VisionFacts, *, mode: str, max_pages: int) -> VisionEligibility:
    """Which parsed PDF versions the vision reader may read (pure)."""

    if mode not in {"ai_assisted", "ai_first"}:
        return VisionEligibility(False, "MODE_OFF")
    if (
        facts.doc_type not in WIRED_DOC_TYPES
        or facts.file_format != "pdf"
        or facts.extraction_method not in PDF_METHODS
    ):
        return VisionEligibility(False, "NOT_PDF")
    if facts.page_count < 1:
        return VisionEligibility(False, "NO_PAGES")
    if facts.page_count > max_pages:
        return VisionEligibility(False, "TOO_MANY_PAGES")
    if mode == "ai_assisted" and not facts.needs_verification:
        return VisionEligibility(False, "NOT_NEEDED")
    return VisionEligibility(True)


# --------------------------------------------------------------------------- subject


@dataclass(frozen=True, slots=True)
class VisionSubject:
    document_id: UUID
    version_id: UUID
    doc_type: DocTypeName
    content_hash: str
    pages: tuple[int, ...]
    facts: VisionFacts
    object_key: str | None
    current: bool
    """The version is still the document's current version and it is not withdrawn."""


def _version_pages(connection: Connection, workspace_id: UUID, version_id: UUID) -> tuple[int, ...]:
    return tuple(
        int(page)
        for page in connection.scalars(
            sa.select(TEXTS.c.page)
            .where(TEXTS.c.workspace_id == workspace_id, TEXTS.c.document_version_id == version_id)
            .order_by(TEXTS.c.page)
        )
    )


def load_subject(
    connection: Connection, workspace_id: UUID, version_id: UUID, *, for_update: bool = False
) -> VisionSubject | None:
    query = (
        sa.select(
            VERSIONS.c.document_id,
            VERSIONS.c.content_hash,
            VERSIONS.c.extraction_method,
            VERSIONS.c.needs_verification,
            DOCUMENTS.c.doc_type,
            DOCUMENTS.c.current_version_id,
            DOCUMENTS.c.withdrawn_at,
            SOURCES.c.format,
            SOURCES.c.object_key,
        )
        .select_from(
            VERSIONS.join(
                DOCUMENTS,
                sa.and_(
                    DOCUMENTS.c.workspace_id == VERSIONS.c.workspace_id,
                    DOCUMENTS.c.id == VERSIONS.c.document_id,
                ),
            ).outerjoin(
                SOURCES,
                sa.and_(
                    SOURCES.c.workspace_id == VERSIONS.c.workspace_id,
                    SOURCES.c.id == VERSIONS.c.source_file_id,
                ),
            )
        )
        .where(VERSIONS.c.workspace_id == workspace_id, VERSIONS.c.id == version_id)
    )
    if for_update:
        query = query.with_for_update(of=DOCUMENTS)
    row = connection.execute(query).first()
    if row is None:
        return None
    pages = _version_pages(connection, workspace_id, version_id)
    facts = VisionFacts(
        doc_type=str(row.doc_type),
        file_format=str(row.format or ""),
        extraction_method=row.extraction_method,
        needs_verification=bool(row.needs_verification),
        page_count=len(pages),
    )
    return VisionSubject(
        document_id=UUID(str(row.document_id)),
        version_id=version_id,
        doc_type=cast(DocTypeName, row.doc_type),
        content_hash=str(row.content_hash),
        pages=pages,
        facts=facts,
        object_key=row.object_key,
        current=row.current_version_id == version_id and row.withdrawn_at is None,
    )


def eligibility(subject: VisionSubject, *, mode: str, limits: AiLimits) -> VisionEligibility:
    return vision_eligibility(
        subject.facts, mode=mode, max_pages=limits.for_kind(HOOK).max_pages or 0
    )


# --------------------------------------------------------------------------- page images


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """One masked page image as the sandbox returns it."""

    page: int
    mime_type: Literal["image/jpeg", "image/png"]
    data: bytes = field(repr=False)
    sha256: str
    width: int
    height: int
    render: Mapping[str, Any]
    mask: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PageRow:
    page: int
    sha256: str
    mime_type: Literal["image/jpeg", "image/png"]
    width: int
    height: int
    render: Mapping[str, Any]
    mask: Mapping[str, Any]
    data: bytes | None = field(default=None, repr=False)

    @property
    def sent_page(self) -> SentPage:
        boxes = tuple(
            box
            for raw in self.mask.get("boxes", [])
            if isinstance(raw, list) and (box := Box.parse(raw)) is not None
        )
        return SentPage(self.page, boxes)


def fingerprint(pages: Sequence[PageRow | RenderedPage]) -> str:
    """Identity of what the reader sees: page numbers, image hashes, mask and render versions."""

    document = {
        "mask": IMAGE_MASK_VERSION,
        "render": VISION_RENDER.version,
        "pages": [[page.page, page.sha256] for page in sorted(pages, key=lambda p: p.page)],
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_pages(
    connection: Connection, workspace_id: UUID, version_id: UUID, pages: Sequence[RenderedPage]
) -> None:
    for page in pages:
        values = {
            "image_sha256": page.sha256,
            "mime_type": page.mime_type,
            "width": page.width,
            "height": page.height,
            "byte_size": len(page.data),
            "render": dict(page.render),
            "mask": dict(page.mask),
            "image": page.data,
            "purged_at": None,
        }
        connection.execute(
            postgres_insert(PAGES)
            .values(
                document_version_id=version_id,
                page=page.page,
                workspace_id=workspace_id,
                **values,
            )
            .on_conflict_do_update(index_elements=["document_version_id", "page"], set_=values)
        )


def load_pages(
    connection: Connection, workspace_id: UUID, version_id: UUID, *, with_bytes: bool = False
) -> list[PageRow]:
    columns = [
        PAGES.c.page,
        PAGES.c.image_sha256,
        PAGES.c.mime_type,
        PAGES.c.width,
        PAGES.c.height,
        PAGES.c.render,
        PAGES.c.mask,
    ]
    if with_bytes:
        columns.append(PAGES.c.image)
    rows = connection.execute(
        sa.select(*columns)
        .where(PAGES.c.workspace_id == workspace_id, PAGES.c.document_version_id == version_id)
        .order_by(PAGES.c.page)
    ).mappings()
    return [
        PageRow(
            page=int(row["page"]),
            sha256=str(row["image_sha256"]),
            mime_type=cast(Literal["image/jpeg", "image/png"], row["mime_type"]),
            width=int(row["width"]),
            height=int(row["height"]),
            render=dict(row["render"]),
            mask=dict(row["mask"]),
            data=bytes(row["image"]) if with_bytes and row["image"] is not None else None,
        )
        for row in rows
    ]


def purge_version_pages(
    connection: Connection, workspace_id: UUID, version_id: UUID, now: datetime
) -> int:
    result = connection.execute(
        sa.update(PAGES)
        .where(
            PAGES.c.workspace_id == workspace_id,
            PAGES.c.document_version_id == version_id,
            PAGES.c.image.is_not(None),
        )
        .values(image=None, purged_at=now)
    )
    return int(result.rowcount or 0)


def purge_idle_pages(engine: Engine, *, now: datetime) -> int:
    """Clear image bytes of versions that no queued, reserved or sending vision request uses."""

    live = (
        sa.select(REQUESTS.c.id)
        .where(
            REQUESTS.c.workspace_id == PAGES.c.workspace_id,
            REQUESTS.c.hook == HOOK,
            REQUESTS.c.subject_type == SUBJECT_TYPE,
            REQUESTS.c.subject_id == PAGES.c.document_version_id,
            REQUESTS.c.request_state.in_(("queued", "reserved", "sending")),
        )
        .exists()
    )
    consensus_pending = (
        sa.select(CONSENSUS_RUNS.c.id)
        .where(
            CONSENSUS_RUNS.c.workspace_id == PAGES.c.workspace_id,
            CONSENSUS_RUNS.c.document_version_id == PAGES.c.document_version_id,
            CONSENSUS_RUNS.c.status == "queued",
        )
        .exists()
    )
    with engine.begin() as connection:
        result = connection.execute(
            sa.update(PAGES)
            .where(PAGES.c.image.is_not(None), ~live, ~consensus_pending)
            .values(image=None, purged_at=now)
        )
    return int(result.rowcount or 0)


def provider_images(
    pages: Sequence[PageRow], resolution: MediaResolution
) -> tuple[ProviderImage, ...] | None:
    """The images to send, or ``None`` when a page's bytes are gone or do not match its hash."""

    images: list[ProviderImage] = []
    for page in pages:
        if page.data is None or hashlib.sha256(page.data).hexdigest() != page.sha256:
            return None
        images.append(
            ProviderImage(
                caption=f"Page {page.page}",
                mime_type=page.mime_type,
                data=page.data,
                sha256=page.sha256,
                resolution=resolution,
            )
        )
    return tuple(images)


def prompt_for(
    doc_type: DocTypeName, pages: Sequence[PageRow], reader: VisionReaderInfo
) -> PromptPayload | None:
    """The reader's prompt variant and schema dialect over these page images."""

    images = provider_images(pages, reader.media_resolution)
    if not images:
        return None
    return vision_prompt(
        doc_type, images, variant=reader.prompt_variant, dialect=reader.schema_dialect
    )


def page_prompts(
    doc_type: DocTypeName, pages: Sequence[PageRow], reader: VisionReaderInfo
) -> tuple[PromptPayload, ...] | None:
    """One payload per page for a reader that reads one page per call (WI-BE2).

    Each payload is the reader's prompt over a single page image; ``None`` when any
    page's bytes are gone. A reader without ``pages_per_call`` gets no split.
    """

    if reader.pages_per_call is None:
        return ()
    if reader.pages_per_call != 1:
        raise ValueError("only one page per call is supported")
    payloads: list[PromptPayload] = []
    for page in pages:
        payload = prompt_for(doc_type, [page], reader)
        if payload is None:
            return None
        payloads.append(payload)
    return tuple(payloads)


def merge_page_answers(output_texts: Sequence[str], *, pages: Sequence[int]) -> str:
    """The per-page answers as one ``vision.schema.v4`` answer (JSON text).

    Each answer's top level must parse on its own; items placed on a page other than
    the one sent are dropped by the core merge. The other items are merged as
    answered, so the parse of the merged answer is the one place a schema-breaking
    item is dropped and counted (D73).
    """

    if len(output_texts) != len(pages):
        raise InvalidOutput("invalid_output", "one answer per page is required")
    answers = []
    for page, text in zip(pages, output_texts, strict=True):
        parse_output(HOOK, text)
        answers.append((page, json.loads(text)))
    merged = merge_page_readings(answers)
    if merged.dropped_off_page:
        _LOGGER.info("vision_page_merge dropped_off_page=%d", merged.dropped_off_page)
    return json.dumps(merged.answer, ensure_ascii=False, separators=(",", ":"))


def band_prompt(
    doc_type: DocTypeName, band: PageRow, reader: VisionReaderInfo
) -> PromptPayload | None:
    """The reader's band prompt over page 1's header band (WI-Q2), or ``None``."""

    if reader.band_prompt_variant is None:
        return None
    images = provider_images([band], reader.media_resolution)
    if not images:
        return None
    return vision_prompt(
        doc_type, images, variant=reader.band_prompt_variant, dialect=reader.schema_dialect
    )


def _band_box(box: object, top: int, order: BoxOrder) -> list[int] | None:
    """A band box on the page's 0-1000 scale: only the vertical edges are rescaled."""

    if not (isinstance(box, list) and len(box) == 4 and all(type(v) is int for v in box)):
        return None
    moved = list(box)
    for index in (0, 2) if order == "yx" else (1, 3):
        moved[index] = round(moved[index] * top / 1000)
    return moved


def _note_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return (str(item.get("looks_like", "")), "".join(str(item.get("text", "")).split()).upper())


def merge_answers_with_band(
    output_texts: Sequence[str],
    *,
    pages: Sequence[int],
    band_page: int,
    band_top: int = BAND_TOP,
    box_order: BoxOrder = "yx",
) -> str:
    """Per-page answers plus a last header-band answer as one answer (WI-Q2, D93).

    The page answers merge as :func:`merge_page_answers`. From the band answer only
    its annotations are taken (handwritten or stamped text, never a field or a line),
    moved to page ``band_page`` with their boxes rescaled from the band to the page;
    one the page answers already hold (same type and text) is not added twice. A band
    answer that does not parse is ignored and logged: the band adds hints, it never
    fails a reading.
    """

    if len(output_texts) != len(pages) + 1:
        raise InvalidOutput("invalid_output", "one answer per page and one band answer")
    merged = json.loads(merge_page_answers(output_texts[:-1], pages=pages))
    try:
        parse_output(HOOK, output_texts[-1])
        band = json.loads(output_texts[-1])
    except (InvalidOutput, ValueError) as exc:
        _LOGGER.info("vision_band_ignored reason=%s", getattr(exc, "code", type(exc).__name__))
        return json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    notes = merged.setdefault("annotations", [])
    seen = {_note_key(item) for item in notes if isinstance(item, Mapping)}
    added = dropped = 0
    raw = band.get("annotations") if isinstance(band, Mapping) else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            dropped += 1
            continue
        box = _band_box(item.get("box_2d"), band_top, box_order)
        # The band call saw one image, sent as page 1 of the document.
        if box is None or type(item.get("page")) is not int or item["page"] != band_page:
            dropped += 1
            continue
        if _note_key(item) in seen:
            continue
        seen.add(_note_key(item))
        notes.append({**dict(item), "page": band_page, "box_2d": box})
        added += 1
    _LOGGER.info("vision_band_merge added=%d dropped=%d", added, dropped)
    return json.dumps(merged, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------- checking


@dataclass(frozen=True, slots=True)
class VisionContext:
    """Everything the checks need besides the answer itself."""

    doc_type: DocTypeName
    pages: Mapping[int, SentPage]
    buyer_entities: tuple[BuyerEntityHint, ...] = ()
    gst_rates: tuple[Decimal, ...] = ()
    ocr: Document | None = None
    as_of: date | None = None
    """Day the D42 date window is measured from; ``None`` skips that check."""
    box_order: BoxOrder = "yx"
    """Coordinate order the reader returns; ``xy`` boxes are swapped before any check."""


@dataclass(frozen=True, slots=True)
class VisionValidation:
    check: VisionCheck
    document: Document

    @property
    def accepted(self) -> bool:
        return self.check.accepted_count > 0


def _yx(box: Sequence[int], order: BoxOrder) -> tuple[int, ...]:
    """``[ymin, xmin, ymax, xmax]``; an ``xy`` box ``[xmin, ymin, xmax, ymax]`` is swapped."""

    if order == "xy" and len(box) == 4:
        return (box[1], box[0], box[3], box[2])
    return tuple(box)


def reading_from_output(output: VisionOutput, *, box_order: BoxOrder = "yx") -> VisionReading:
    return VisionReading(
        doc_kind=output.doc_kind,
        pages=tuple(VisionPageKind(item.page, item.kind, item.title) for item in output.pages),
        fields=tuple(
            VisionField(item.name, item.text, item.page, _yx(item.box_2d, box_order), item.label)
            for item in output.fields
        ),
        lines=tuple(
            VisionLine(
                item.page,
                _yx(item.box_2d, box_order),
                {
                    name: value
                    for name, value in item.model_dump(exclude={"page", "box_2d"}).items()
                    if isinstance(value, str)
                },
            )
            for item in output.lines
        ),
        annotations=tuple(
            VisionAnnotation(
                item.kind, item.text, item.looks_like, item.page, _yx(item.box_2d, box_order)
            )
            for item in output.annotations
        ),
        unreadable=tuple(
            VisionUnreadable(item.name, item.page, _yx(item.box_2d, box_order))
            for item in output.unreadable
        ),
    )


def validate_vision(output: VisionOutput, context: VisionContext) -> VisionValidation:
    """Check an answer; raise ``ungrounded`` when it had readings and none survived."""

    reading = reading_from_output(output, box_order=context.box_order)
    check = check_reading(
        reading,
        pages=context.pages,
        buyer_entities=context.buyer_entities,
        ocr=context.ocr,
        as_of=context.as_of,
    )
    if output.schema_dropped:
        dropped = Counter(check.dropped)
        dropped.update(output.schema_dropped)
        check = replace(check, dropped=MappingProxyType(dict(dropped)))
    if reading.item_count and not check.accepted_count:
        raise InvalidOutput("ungrounded", "no reading survived the page, box and rule checks")
    document = vision_document(
        check,
        DocType(context.doc_type),
        buyer_entities=context.buyer_entities,
        gst_rates=context.gst_rates or None,
    )
    return VisionValidation(check, document)


def party_hints(
    connection: Connection, workspace_id: UUID
) -> tuple[tuple[BuyerEntityHint, ...], tuple[Decimal, ...]]:
    body = connection.scalar(
        sa.select(CONFIG_VERSIONS.c.body)
        .select_from(
            CONFIG_ACTIVE.join(
                CONFIG_VERSIONS,
                sa.and_(
                    CONFIG_ACTIVE.c.workspace_id == CONFIG_VERSIONS.c.workspace_id,
                    CONFIG_ACTIVE.c.config_version_id == CONFIG_VERSIONS.c.id,
                ),
            )
        )
        .where(CONFIG_ACTIVE.c.workspace_id == workspace_id)
    )
    engine_body = body.get("engine", {}) if isinstance(body, Mapping) else {}
    hints = party_hints_from_config(engine_body if isinstance(engine_body, Mapping) else {})
    return tuple(hints.get("buyer_entities", ())), tuple(hints.get("gst_rates", ()))


_OCR_HEADER = {
    "vendor_gstin": "vendor_gstin",
    "taxable_total": "taxable_total",
    "cgst": "cgst_total",
    "sgst": "sgst_total",
    "igst": "igst_total",
    "round_off": "round_off",
    "grand_total": "grand_total",
}


def _reading(value: object) -> dict[str, Any]:
    return {"value": value, "confidence": Decimal("0.9"), "source": "rule", "evidence": {}}


def ocr_document(
    connection: Connection, workspace_id: UUID, version_id: UUID, doc_type: DocTypeName
) -> Document | None:
    """The stored rules reading of a version (totals, supplier GSTIN, priced lines)."""

    row = (
        connection.execute(
            sa.select(VERSIONS).where(
                VERSIONS.c.workspace_id == workspace_id, VERSIONS.c.id == version_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    header: dict[str, Any] = {"doc_type": doc_type}
    for name, column in _OCR_HEADER.items():
        if row[column] is not None:
            header[name] = _reading(row[column])
    lines = [
        {
            "line_no": int(line["line_no"]),
            **{
                name: _reading(line[name])
                for name in ("quantity", "rate", "amount")
                if line[name] is not None
            },
        }
        for line in connection.execute(
            sa.select(LINES.c.line_no, LINES.c.quantity, LINES.c.rate, LINES.c.amount).where(
                LINES.c.workspace_id == workspace_id, LINES.c.document_version_id == version_id
            )
        ).mappings()
    ]
    return Document.model_validate({"header": header, "lines": lines})


def context_for(
    connection: Connection,
    workspace_id: UUID,
    version_id: UUID,
    doc_type: DocTypeName,
    pages: Sequence[PageRow],
    *,
    box_order: BoxOrder = "yx",
) -> VisionContext:
    buyers, rates = party_hints(connection, workspace_id)
    return VisionContext(
        doc_type=doc_type,
        pages={page.page: page.sent_page for page in pages},
        buyer_entities=buyers,
        gst_rates=rates,
        ocr=ocr_document(connection, workspace_id, version_id, doc_type),
        as_of=datetime.now(UTC).date(),
        box_order=box_order,
    )


__all__ = [
    "CORRECTABLE_DOC_TYPES",
    "HOOK",
    "SKIP_NOT_ELIGIBLE",
    "SKIP_PAGES_FAILED",
    "SKIP_PAGES_MISSING",
    "SKIP_READER_OFF",
    "SKIP_SOURCE_CHANGED",
    "SUBJECT_TYPE",
    "SUGGESTION_LABELS",
    "WIRED_DOC_TYPES",
    "PageRow",
    "RenderedPage",
    "VisionContext",
    "VisionEligibility",
    "VisionFacts",
    "VisionSubject",
    "VisionValidation",
    "band_prompt",
    "context_for",
    "eligibility",
    "fingerprint",
    "load_pages",
    "load_subject",
    "merge_answers_with_band",
    "merge_page_answers",
    "ocr_document",
    "page_prompts",
    "party_hints",
    "prompt_for",
    "provider_images",
    "purge_idle_pages",
    "purge_version_pages",
    "reading_from_output",
    "store_pages",
    "validate_vision",
    "vision_eligibility",
]
