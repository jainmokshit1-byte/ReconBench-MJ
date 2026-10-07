"""H1 PDF extraction suggestions: the subject a request refers to and the text it sends.

Shared by the producer (PDF parse), the AI worker and the review view. The
subject is one immutable parser version of a PDF invoice; its stored page text
is sent with e-mail, phone, PAN and banking details replaced by placeholders
(GSTINs stay, because they are invoice fields). Output is suggestions only: they
are re-grounded against the current page text whenever they are shown or used,
and a value reaches a document only through a person's correction save.

PO and GRN eligibility rules are *proposed* (decision D13) and stay unwired.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.ai.contracts import DocTypeName
from reconbench_app.ai.h1_eligibility import H1Eligibility, H1Facts, h1_eligibility
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.redaction import H1_CATEGORIES, Category, RedactedText, redact
from reconbench_app.db.metadata import metadata

HOOK = "h1_extract"
SUBJECT_TYPE = "document_version"
SKIP_SOURCE_CHANGED = "SOURCE_CHANGED"
SKIP_NOT_ELIGIBLE = "NOT_ELIGIBLE"
# D13: PO/GRN defaults await Product approval, so only invoices are wired.
WIRED_DOC_TYPES: frozenset[str] = frozenset({"invoice"})
# ai_first suggestions carry their own label so a reviewer can tell them apart (D12).
SUGGESTION_LABELS: dict[str, str] = {
    "ai_assisted": "AI suggestion: fields the rules could not read reliably",
    "ai_first": "AI-first suggestion: requested for every PDF; rules output stays authoritative",
}

DOCUMENTS = metadata.tables["document"]
VERSIONS = metadata.tables["document_version"]
TEXTS = metadata.tables["document_text"]
LINES = metadata.tables["doc_line"]
SOURCES = metadata.tables["source_file"]


@dataclass(frozen=True, slots=True)
class H1Subject:
    document_id: UUID
    version_id: UUID
    doc_type: DocTypeName
    content_hash: str
    facts: H1Facts
    current: bool
    """The version is still the document's current version and it is not withdrawn."""


def load_pages(
    connection: Connection, workspace_id: UUID, version_id: UUID
) -> list[tuple[int, str]]:
    return [
        (int(row.page), str(row.content))
        for row in connection.execute(
            sa.select(TEXTS.c.page, TEXTS.c.content)
            .where(TEXTS.c.workspace_id == workspace_id, TEXTS.c.document_version_id == version_id)
            .order_by(TEXTS.c.page)
        )
    ]


def redacted_pages(pages: Sequence[tuple[int, str]]) -> dict[int, RedactedText]:
    """Redact every page with one numbering, exactly as the prompt was built."""

    numbering: dict[tuple[Category, str], str] = {}
    return {number: redact(text, H1_CATEGORIES, numbering=numbering) for number, text in pages}


def load_subject(
    connection: Connection,
    workspace_id: UUID,
    version_id: UUID,
    *,
    for_update: bool = False,
) -> H1Subject | None:
    query = (
        sa.select(
            VERSIONS.c.document_id,
            VERSIONS.c.content_hash,
            VERSIONS.c.extraction_method,
            VERSIONS.c.extraction_conf,
            VERSIONS.c.needs_verification,
            VERSIONS.c.doc_no,
            VERSIONS.c.doc_date,
            VERSIONS.c.vendor_name,
            VERSIONS.c.vendor_gstin,
            DOCUMENTS.c.doc_type,
            DOCUMENTS.c.current_version_id,
            DOCUMENTS.c.withdrawn_at,
            SOURCES.c.format,
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
    usable_lines = connection.scalar(
        sa.select(sa.func.count())
        .select_from(LINES)
        .where(
            LINES.c.workspace_id == workspace_id,
            LINES.c.document_version_id == version_id,
            LINES.c.quantity.is_not(None),
            sa.or_(LINES.c.rate.is_not(None), LINES.c.amount.is_not(None)),
        )
    )
    sizes = connection.execute(
        sa.select(
            sa.func.count(),
            sa.func.coalesce(sa.func.sum(sa.func.char_length(TEXTS.c.content)), 0),
            sa.func.coalesce(sa.func.sum(sa.func.octet_length(TEXTS.c.content)), 0),
        ).where(TEXTS.c.workspace_id == workspace_id, TEXTS.c.document_version_id == version_id)
    ).one()
    facts = H1Facts(
        doc_type=cast(DocTypeName, row.doc_type),
        file_format=str(row.format or ""),
        extraction_method=row.extraction_method,
        needs_verification=bool(row.needs_verification),
        has_doc_no=bool(row.doc_no),
        has_doc_date=row.doc_date is not None,
        has_vendor=bool(row.vendor_name or row.vendor_gstin),
        has_usable_lines=bool(usable_lines),
        min_critical_confidence=(
            Decimal(str(row.extraction_conf)) if row.extraction_conf is not None else None
        ),
        page_count=int(sizes[0]),
        text_chars=int(sizes[1]),
        text_bytes=int(sizes[2]),
    )
    return H1Subject(
        document_id=UUID(str(row.document_id)),
        version_id=version_id,
        doc_type=cast(DocTypeName, row.doc_type),
        content_hash=str(row.content_hash),
        facts=facts,
        current=row.current_version_id == version_id and row.withdrawn_at is None,
    )


def eligibility(subject: H1Subject, *, mode: str, limits: AiLimits) -> H1Eligibility:
    if subject.doc_type not in WIRED_DOC_TYPES:
        return H1Eligibility(False, "NOT_NEEDED")
    return h1_eligibility(
        subject.facts,
        mode=mode,
        max_pages=limits.h1_extract.max_pages or 0,
        max_input_bytes=limits.h1_extract.max_input_bytes,
    )


__all__ = [
    "HOOK",
    "SKIP_NOT_ELIGIBLE",
    "SKIP_SOURCE_CHANGED",
    "SUGGESTION_LABELS",
    "SUBJECT_TYPE",
    "WIRED_DOC_TYPES",
    "H1Subject",
    "eligibility",
    "load_pages",
    "load_subject",
    "redacted_pages",
]
